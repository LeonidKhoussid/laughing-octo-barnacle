"""Redis-backed Vault for multiworker deployments.

Implements the Vault interface with a shared Redis store. Each record is stored
as a Redis hash:

    pii:vault:{namespace}:{record_id} -> {
        payload: <encrypted record>,
        lease_owner: <uuid>,
        state: <PROCESSING|READY|RESTORED>,
        lease_expires_at: <epoch seconds>
    }

The `payload` field holds the full record (state, fingerprint, masked_text,
mapping, timestamps, lease) serialized to JSON and encrypted with AeadCipher
(AES-GCM). Associated data binds the ciphertext to (namespace, record_id) so a
ciphertext cannot be moved between contexts. The `lease_owner`, `state`, and
`lease_expires_at` fields are stored in plaintext so the atomic Lua scripts can
perform compare-and-swap on the lease without decrypting (no NLP or heavy loops
run inside Redis).

A single connection pool is shared across calls (thread-safe). Graceful shutdown
closes the pool. If the AEAD key is missing, construction fails (readiness error)
rather than silently disabling encryption.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

import redis

from app.vault.base import Vault, VaultCapacityError, VaultIntegrityError, VaultUnavailableError
from app.vault.crypto import AeadCipher, CryptoError

_KEY_PREFIX = "pii:vault"
_META_PREFIX = "pii:vault:meta"

_CLAIM_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  redis.call('HSET', KEYS[1], 'payload', ARGV[1], 'lease_owner', ARGV[2],
             'state', ARGV[3], 'lease_expires_at', ARGV[4])
  redis.call('EXPIRE', KEYS[1], ARGV[5])
  return 1
else
  return 0
end
"""

_RECLAIM_SCRIPT = """
local state = redis.call('HGET', KEYS[1], 'state')
local lease_exp = redis.call('HGET', KEYS[1], 'lease_expires_at')
if state == 'PROCESSING' and (lease_exp == false or tonumber(lease_exp) < tonumber(ARGV[4])) then
  redis.call('HSET', KEYS[1], 'payload', ARGV[1], 'lease_owner', ARGV[2],
             'state', ARGV[3], 'lease_expires_at', ARGV[5])
  redis.call('EXPIRE', KEYS[1], ARGV[6])
  return 1
else
  return 0
end
"""

_COMMIT_SCRIPT = """
local owner = redis.call('HGET', KEYS[1], 'lease_owner')
if owner == ARGV[2] then
  redis.call('HSET', KEYS[1], 'payload', ARGV[1], 'state', ARGV[3])
  redis.call('EXPIRE', KEYS[1], ARGV[4])
  return 1
else
  return 0
end
"""

_RELEASE_SCRIPT = """
local owner = redis.call('HGET', KEYS[1], 'lease_owner')
if owner == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
else
  return 0
end
"""


class RedisVault(Vault):
    """Namespace-scoped, encrypted, connection-pooled Redis vault."""

    def __init__(
        self,
        cipher: AeadCipher,
        url: str = "redis://127.0.0.1:6379/0",
        max_entries: int = 100000,
        max_bytes: int = 1024 * 1024 * 1024,
        default_ttl_seconds: int | None = 3600,
        socket_timeout: float = 2.0,
    ) -> None:
        self._cipher = cipher
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._default_ttl = default_ttl_seconds
        self._client = redis.Redis.from_url(
            url,
            decode_responses=False,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
        )
        self._claim = self._client.register_script(_CLAIM_SCRIPT)
        self._reclaim = self._client.register_script(_RECLAIM_SCRIPT)
        self._commit = self._client.register_script(_COMMIT_SCRIPT)
        self._release = self._client.register_script(_RELEASE_SCRIPT)
        self._closed = False

    # -- key / encode helpers -------------------------------------------------

    @staticmethod
    def _key(namespace: str, key: str) -> str:
        return f"{_KEY_PREFIX}:{namespace}:{key}"

    def _encode(self, record: Any, namespace: str, record_id: str) -> bytes:
        plaintext = json.dumps(record, ensure_ascii=False).encode("utf-8")
        ct = self._cipher.encrypt(plaintext, namespace, record_id)
        return base64.b64encode(ct)

    def _decode(self, raw: bytes, namespace: str, record_id: str) -> Any:
        try:
            ct = base64.b64decode(raw, validate=True)
            plaintext = self._cipher.decrypt(ct, namespace, record_id)
            return json.loads(plaintext.decode("utf-8"))
        except (CryptoError, ValueError, json.JSONDecodeError) as exc:
            raise VaultIntegrityError("stored record failed integrity/context check") from exc

    def _ttl(self, ttl_seconds: int | None) -> int | None:
        return self._default_ttl if ttl_seconds is None else ttl_seconds

    @staticmethod
    def _wrap(fn, *args, **kwargs):
        """Run a Redis call, converting connection errors to VaultUnavailableError."""
        try:
            return fn(*args, **kwargs)
        except redis.RedisError as exc:
            raise VaultUnavailableError("vault backend unavailable") from exc

    # -- Vault interface ------------------------------------------------------

    def put(self, namespace: str, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ttl = self._ttl(ttl_seconds)
        payload = self._encode(value, namespace, key)
        full_key = self._key(namespace, key)
        state = value.get("state", "") if isinstance(value, dict) else ""
        lease_exp = value.get("lease_expires_at", "") if isinstance(value, dict) else ""
        owner = value.get("lease_owner", "") if isinstance(value, dict) else ""
        pipe = self._client.pipeline(transaction=True)
        pipe.hset(
            full_key,
            mapping={
                "payload": payload,
                "lease_owner": owner,
                "state": state,
                "lease_expires_at": lease_exp,
            },
        )
        if ttl is not None:
            pipe.expire(full_key, ttl)
        self._wrap(pipe.execute)

    def get(self, namespace: str, key: str) -> Any | None:
        raw = self._wrap(self._client.hget, self._key(namespace, key), "payload")
        if raw is None:
            return None
        return self._decode(raw, namespace, key)

    def delete(self, namespace: str, key: str) -> None:
        self._wrap(self._client.delete, self._key(namespace, key))

    def expire(self, namespace: str, key: str, ttl_seconds: int) -> None:
        self._wrap(self._client.expire, self._key(namespace, key), ttl_seconds)

    def size(self) -> int:
        count = 0
        cursor = 0
        while True:
            cursor, keys = self._wrap(
                self._client.scan, cursor=cursor, match=f"{_KEY_PREFIX}:*", count=500
            )
            for k in keys:
                if not k.startswith(_META_PREFIX.encode()):
                    count += 1
            if cursor == 0:
                break
        return count

    def size_bytes(self) -> int:
        total = 0
        cursor = 0
        while True:
            cursor, keys = self._wrap(
                self._client.scan, cursor=cursor, match=f"{_KEY_PREFIX}:*", count=500
            )
            for k in keys:
                if k.startswith(_META_PREFIX.encode()):
                    continue
                v = self._wrap(self._client.hget, k, "payload")
                if v is not None:
                    total += len(v)
            if cursor == 0:
                break
        return total

    def ensure_capacity(self, extra_bytes: int) -> None:
        if self.size() >= self._max_entries:
            raise VaultCapacityError("vault at max entries")
        if self.size_bytes() + extra_bytes > self._max_bytes:
            raise VaultCapacityError("vault at max bytes")

    def atomic_claim(
        self, namespace: str, key: str, record: Any, ttl_seconds: int
    ) -> tuple[bool, Any]:
        payload = self._encode(record, namespace, key)
        full_key = self._key(namespace, key)
        owner = record.get("lease_owner", "") if isinstance(record, dict) else ""
        state = record.get("state", "") if isinstance(record, dict) else ""
        lease_exp = record.get("lease_expires_at", "") if isinstance(record, dict) else ""
        res = self._wrap(
            self._claim, keys=[full_key], args=[payload, owner, state, lease_exp, ttl_seconds]
        )
        if res == 1:
            return True, record
        existing = self.get(namespace, key)
        return False, existing

    def atomic_reclaim(
        self, namespace: str, key: str, record: Any, ttl_seconds: int, now: float
    ) -> bool:
        payload = self._encode(record, namespace, key)
        full_key = self._key(namespace, key)
        owner = record.get("lease_owner", "") if isinstance(record, dict) else ""
        state = record.get("state", "") if isinstance(record, dict) else ""
        lease_exp = record.get("lease_expires_at", "") if isinstance(record, dict) else ""
        res = self._wrap(
            self._reclaim,
            keys=[full_key],
            args=[payload, owner, state, now, lease_exp, ttl_seconds],
        )
        return res == 1

    def atomic_commit(
        self, namespace: str, key: str, record: Any, owner_id: str, ttl_seconds: int
    ) -> bool:
        payload = self._encode(record, namespace, key)
        full_key = self._key(namespace, key)
        state = record.get("state", "") if isinstance(record, dict) else ""
        res = self._wrap(
            self._commit, keys=[full_key], args=[payload, owner_id, state, ttl_seconds]
        )
        return res == 1

    def atomic_release(self, namespace: str, key: str, owner_id: str) -> bool:
        full_key = self._key(namespace, key)
        res = self._wrap(self._release, keys=[full_key], args=[owner_id])
        return res == 1

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except redis.RedisError:
            return False
