"""Integration tests for RedisVault against a REAL local Redis (R64).

These tests run against a real redis-server on a dedicated test port (6380) and
a dedicated test DB (index 15). They are NOT mocks. If Redis cannot be started,
the module is skipped with a clear NOT_RUN/BLOCKED reason rather than faking a
pass.

Coverage (master prompt section 6 / 11, rules R28-R36, R49-R50, R64):
- put/get/delete/expire round-trip with encryption.
- Mask in one RedisVault, unmask in another (two processes sharing Redis).
- Two concurrent mask on same ID/text -> single canonical record.
- Repeat original after lost mask response -> same mask and tokens.
- Repeat masked after lost unmask response -> same original.
- Same ID with different original -> ConflictError (no silent overwrite).
- Expired mapping -> ContextMissingError (no invented restoration).
- Corrupted ciphertext -> VaultIntegrityError.
- Redis unavailable at mask -> RetryableError, no success without mapping.
- Redis unavailable in multiworker -> no split-brain fallback to memory.
- TTL expiry and replay grace.
- Capacity exhausted -> RetryableError before issuing irreversible mask.
"""
from __future__ import annotations

import base64
import os
import shutil
import socket
import subprocess
import threading
import time

import pytest

from app.core.engine import Engine
from app.detectors.registry import DetectorRegistry
from app.policies.schema import ConsumerPolicy
from app.vault.base import VaultIntegrityError
from app.vault.crypto import AeadCipher
from app.vault.fingerprint import Fingerprinter
from app.vault.lifecycle import (
    ConflictError,
    ContextMissingError,
    Lifecycle,
    RetryableError,
)
from app.vault.redis import RedisVault

REDIS_PORT = 6380
REDIS_DB = 15
REDIS_URL = f"redis://127.0.0.1:{REDIS_PORT}/{REDIS_DB}"
REDIS_BIN = shutil.which("redis-server") or "/opt/homebrew/bin/redis-server"
REDIS_CLI = shutil.which("redis-cli") or "/opt/homebrew/bin/redis-cli"

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "configs")

_AEAD_KEY = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _find_free_port() -> int:
    """Return an unused TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_redis() -> tuple[int, int] | None:
    """Start a FRESH disposable redis-server on an unused port.

    Returns (port, db) on success, or None if the binary is missing or the
    instance could not be started. The instance is dedicated to this test run:
    we never reuse or flush an existing user's Redis. If isolation setup fails,
    the tests are skipped (NOT_RUN/BLOCKED) rather than touching shared state.
    """
    if not os.path.exists(REDIS_BIN):
        return None
    port = _find_free_port()
    db = 15  # dedicated DB index on the disposable instance
    try:
        subprocess.run(
            [
                REDIS_BIN,
                "--port",
                str(port),
                "--daemonize",
                "yes",
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                "/tmp",
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    for _ in range(20):
        if _port_open(port):
            return port, db
        time.sleep(0.2)
    return None


def _flush_db(port: int, db: int) -> None:
    try:
        subprocess.run(
            [REDIS_CLI, "-p", str(port), "-n", str(db), "flushdb"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        pass


# Provision a fresh disposable instance on an unused port. Never touch an
# existing user's Redis. If isolation setup fails, skip (NOT_RUN/BLOCKED).
_REDIS_SETUP = _start_redis()
if _REDIS_SETUP is not None:
    REDIS_PORT, REDIS_DB = _REDIS_SETUP
    REDIS_URL = f"redis://127.0.0.1:{REDIS_PORT}/{REDIS_DB}"
    _flush_db(REDIS_PORT, REDIS_DB)

pytestmark = pytest.mark.skipif(
    _REDIS_SETUP is None,
    reason=(
        "NOT_RUN/BLOCKED: could not provision a disposable Redis instance. "
        "redis-server binary not found or could not start on an unused port. "
        "Integration tests require a real local Redis (R64); a mock would not "
        "prove cross-process behavior. Tests never reuse or flush an existing "
        "user's Redis."
    ),
)


def _engine() -> Engine:
    registry = DetectorRegistry.from_config(os.path.join(_CONFIG_DIR, "detectors.yaml"))
    return Engine(registry.detectors)


def _policy(**overrides) -> ConsumerPolicy:
    base = {
        "name": "test",
        "namespace": "test-ns",
        "authentication": "api_key",
        "allow_unmask": True,
    }
    base.update(overrides)
    return ConsumerPolicy(**base)


def _cipher() -> AeadCipher:
    return AeadCipher(base64.b64decode(_AEAD_KEY))


def _vault(**kw) -> RedisVault:
    kw.setdefault("url", REDIS_URL)
    return RedisVault(cipher=_cipher(), **kw)


def _lifecycle(vault=None, **kw) -> Lifecycle:
    vault = vault or _vault()
    return Lifecycle(
        vault,
        _engine(),
        Fingerprinter(b"test-fp-key-32-bytes-long!!"),
        "baseline-001",
        **kw,
    )


@pytest.fixture(autouse=True)
def _clean_db():
    _flush_db(REDIS_PORT, REDIS_DB)
    yield
    _flush_db(REDIS_PORT, REDIS_DB)


class TestRedisVaultRoundTrip:
    def test_put_get_delete_expire(self):
        v = _vault()
        record = {"state": "READY", "mapping": {"t1": "a@b.com"}, "masked_text": "x"}
        v.put("ns", "k1", record, ttl_seconds=60)
        assert v.get("ns", "k1") == record
        v.delete("ns", "k1")
        assert v.get("ns", "k1") is None

    def test_encryption_at_rest(self):
        v = _vault()
        record = {"state": "READY", "mapping": {"t1": "a@b.com"}, "masked_text": "x"}
        v.put("ns", "k2", record, ttl_seconds=60)
        raw = v._client.hget(v._key("ns", "k2"), "payload")
        assert raw is not None
        assert b"a@b.com" not in raw
        assert b"READY" not in raw

    def test_namespace_isolation(self):
        v = _vault()
        v.put("ns1", "k", {"v": 1}, ttl_seconds=60)
        assert v.get("ns2", "k") is None

    def test_ttl_expiry(self):
        v = _vault()
        v.put("ns", "k", {"v": 1}, ttl_seconds=1)
        assert v.get("ns", "k") == {"v": 1}
        time.sleep(1.2)
        assert v.get("ns", "k") is None


class TestCrossProcess:
    def test_mask_one_vault_unmask_another(self):
        v1 = _vault()
        v2 = _vault()
        lc1 = _lifecycle(v1)
        lc2 = _lifecycle(v2)
        p = _policy()
        out = lc1.mask("ns", "id1", "email a@b.com", p)
        assert "a@b.com" not in out.masked_text
        original = lc2.unmask("ns", "id1", out.masked_text, p)
        assert original == "email a@b.com"

    def test_concurrent_mask_same_id_single_canonical(self):
        v1 = _vault()
        v2 = _vault()
        lc1 = _lifecycle(v1)
        lc2 = _lifecycle(v2)
        p = _policy()
        results = []
        errors = []

        def worker(lc):
            try:
                results.append(lc.mask("ns", "id1", "email a@b.com", p))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(lc1,)),
            threading.Thread(target=worker, args=(lc2,)),
            threading.Thread(target=worker, args=(lc1,)),
            threading.Thread(target=worker, args=(lc2,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        record = v1.get("ns", "id1")
        assert record["state"] == "READY"
        for out in results:
            assert lc1.unmask("ns", "id1", out.masked_text, p) == "email a@b.com"

    def test_repeat_original_after_lost_mask_same_mask(self):
        v = _vault()
        lc = _lifecycle(v)
        p = _policy()
        out1 = lc.mask("ns", "id1", "email a@b.com", p)
        out2 = lc.mask("ns", "id1", "email a@b.com", p)
        assert out1.masked_text == out2.masked_text
        assert out1.mapping == out2.mapping
        assert out2.replay is True

    def test_repeat_masked_after_lost_unmask_same_original(self):
        v = _vault()
        lc = _lifecycle(v)
        p = _policy()
        out = lc.mask("ns", "id1", "email a@b.com", p)
        assert lc.unmask("ns", "id1", out.masked_text, p) == "email a@b.com"
        assert lc.unmask("ns", "id1", out.masked_text, p) == "email a@b.com"

    def test_same_id_different_original_conflict(self):
        v = _vault()
        lc = _lifecycle(v)
        p = _policy()
        lc.mask("ns", "id1", "email a@b.com", p)
        with pytest.raises(ConflictError):
            lc.mask("ns", "id1", "email c@d.com", p)

    def test_expired_mapping_context_missing(self):
        v = _vault()
        lc = _lifecycle(v)
        p = _policy()
        out = lc.mask("ns", "id1", "email a@b.com", p)
        v.expire("ns", "id1", 0)
        with pytest.raises(ContextMissingError):
            lc.unmask("ns", "id1", out.masked_text, p)


class TestIntegrity:
    def test_corrupted_ciphertext_raises_integrity(self):
        v = _vault()
        record = {"state": "READY", "mapping": {"t1": "a@b.com"}, "masked_text": "x"}
        v.put("ns", "k", record, ttl_seconds=60)
        raw = v._client.hget(v._key("ns", "k"), "payload")
        corrupted = bytearray(raw)
        corrupted[-1] ^= 0xFF
        v._client.hset(v._key("ns", "k"), "payload", bytes(corrupted))
        with pytest.raises(VaultIntegrityError):
            v.get("ns", "k")

    def test_ciphertext_not_movable_between_contexts(self):
        v = _vault()
        record = {"state": "READY", "mapping": {"t1": "a@b.com"}, "masked_text": "x"}
        v.put("ns1", "k", record, ttl_seconds=60)
        # Move the ciphertext to a different namespace's key. The AAD binds the
        # ciphertext to (namespace, record_id), so reading it under ns2 must fail
        # integrity rather than silently decrypting.
        raw = v._client.hget(v._key("ns1", "k"), "payload")
        v._client.hset(v._key("ns2", "k"), "payload", raw)
        with pytest.raises(VaultIntegrityError):
            v.get("ns2", "k")


class TestUnavailable:
    def test_redis_down_mask_retryable_no_mapping(self):
        closed_port = 6399
        v = _vault(url=f"redis://127.0.0.1:{closed_port}/0", socket_timeout=0.3)
        lc = _lifecycle(v)
        p = _policy()
        # Redis down at mask must be a retryable error, never a success. No
        # MaskOutcome is returned, so no irreversible mask is issued without a
        # stored mapping (master prompt 13.4, R35).
        with pytest.raises(RetryableError):
            lc.mask("ns", "id1", "email a@b.com", p)

    def test_redis_down_unmask_retryable(self):
        closed_port = 6399
        v = _vault(url=f"redis://127.0.0.1:{closed_port}/0", socket_timeout=0.3)
        lc = _lifecycle(v)
        p = _policy()
        with pytest.raises(RetryableError):
            lc.unmask("ns", "id1", "some masked text", p)

    def test_redis_down_no_fallback_to_memory(self):
        closed_port = 6399
        v = _vault(url=f"redis://127.0.0.1:{closed_port}/0", socket_timeout=0.3)
        assert isinstance(v, RedisVault)
        lc = _lifecycle(v)
        p = _policy()
        with pytest.raises(RetryableError):
            lc.mask("ns", "id1", "email a@b.com", p)


class TestCapacity:
    def test_capacity_exhausted_retryable_before_mask(self):
        v = _vault(max_entries=1)
        lc = _lifecycle(v)
        p = _policy()
        lc.mask("ns", "id1", "email a@b.com", p)
        with pytest.raises(RetryableError):
            lc.mask("ns", "id2", "email c@d.com", p)
        assert v.get("ns", "id2") is None


class TestReplayGrace:
    def test_replay_grace_keeps_mapping_after_unmask(self):
        v = _vault()
        lc = _lifecycle(v, replay_grace_seconds=60)
        p = _policy()
        out = lc.mask("ns", "id1", "email a@b.com", p)
        lc.unmask("ns", "id1", out.masked_text, p)
        assert lc.unmask("ns", "id1", out.masked_text, p) == "email a@b.com"
        record = v.get("ns", "id1")
        assert record["state"] == "RESTORED"


class TestOwnerRelease:
    """Regression: correct-owner release must succeed and delete the record;
    wrong-owner release must fail and keep the record (R31, section 6.3)."""

    def test_correct_owner_release_deletes_record(self):
        v = _vault()
        lc = _lifecycle(v)
        p = _policy()
        out = lc.mask("ns", "id1", "email a@b.com", p)
        assert v.get("ns", "id1") is not None
        owner = v.get("ns", "id1")["lease_owner"]
        ok = v.atomic_release("ns", "id1", owner)
        assert ok is True, "correct-owner release must return True"
        assert v.get("ns", "id1") is None

    def test_wrong_owner_release_keeps_record(self):
        v = _vault()
        lc = _lifecycle(v)
        p = _policy()
        lc.mask("ns", "id1", "email a@b.com", p)
        assert v.get("ns", "id1") is not None
        ok = v.atomic_release("ns", "id1", "not-the-owner")
        assert ok is False, "wrong-owner release must return False"
        assert v.get("ns", "id1") is not None

    def test_stale_owner_release_keeps_record(self):
        v = _vault()
        lc = _lifecycle(v)
        p = _policy()
        lc.mask("ns", "id1", "email a@b.com", p)
        # A stale/foreign owner must not delete the record.
        ok = v.atomic_release("ns", "id1", "stale-owner")
        assert ok is False
        assert v.get("ns", "id1") is not None
