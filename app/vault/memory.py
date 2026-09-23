"""Thread-safe in-memory Vault for unit tests and single-process local mode.

Supports TTL and max entries/bytes. Not suitable for multiworker (see rules R34).
The atomic_* primitives coordinate the payload_id lifecycle under a per-vault
lock; the critical section is only the per-key state transition, never detection.
"""
from __future__ import annotations

import heapq
import threading
import time
from dataclasses import dataclass

from app.vault.base import Vault, VaultCapacityError


@dataclass
class _Entry:
    value: object
    created_at: float
    expires_at: float | None
    size_bytes: int
    generation: int


class MemoryVault(Vault):
    """Thread-safe in-memory vault with TTL and capacity limits.

    Values retain the existing reference semantics. Byte capacity is an estimate
    charged at each write; callers must write changed values back to update it.
    """

    def __init__(
        self,
        max_entries: int = 100000,
        max_bytes: int = 1024 * 1024 * 1024,
        default_ttl_seconds: int | None = 3600,
    ) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._default_ttl = default_ttl_seconds
        self._lock = threading.RLock()
        self._store: dict[tuple[str, str], _Entry] = {}
        self._bytes = 0
        self._generation = 0
        self._expiry_heap: list[tuple[float, int, tuple[str, str]]] = []

    def _now(self) -> float:
        return time.monotonic()

    def _compact_expiry_heap(self) -> None:
        # Updates leave stale generations behind, never references to old values.
        # Rebuild only after enough updates/deletions to amortize the O(N) work.
        if len(self._expiry_heap) > max(64, 2 * len(self._store)):
            self._expiry_heap = [
                (entry.expires_at, entry.generation, key)
                for key, entry in self._store.items()
                if entry.expires_at is not None
            ]
            heapq.heapify(self._expiry_heap)

    def _purge_expired(self) -> None:
        now = self._now()
        while self._expiry_heap and self._expiry_heap[0][0] <= now:
            _, generation, key = heapq.heappop(self._expiry_heap)
            entry = self._store.get(key)
            if entry is not None and entry.generation == generation:
                self._bytes -= entry.size_bytes
                del self._store[key]
        self._compact_expiry_heap()

    def _index_expiry(self, key: tuple[str, str], entry: _Entry) -> None:
        self._generation += 1
        entry.generation = self._generation
        if entry.expires_at is not None:
            heapq.heappush(self._expiry_heap, (entry.expires_at, entry.generation, key))
        self._compact_expiry_heap()

    @staticmethod
    def _estimate_size(value: object) -> int:
        try:
            return len(str(value).encode("utf-8"))
        except Exception:
            return 0

    def _write(self, key: tuple[str, str], value: object, ttl: int | None) -> None:
        """Validate the whole replacement before changing store or accounting."""
        existing = self._store.get(key)
        if existing is None and len(self._store) >= self._max_entries:
            raise VaultCapacityError("vault at max entries")
        size_bytes = self._estimate_size(value)
        new_bytes = self._bytes - (existing.size_bytes if existing else 0) + size_bytes
        if new_bytes > self._max_bytes:
            raise VaultCapacityError("vault at max bytes")
        now = self._now()
        entry = _Entry(value, now, now + ttl if ttl is not None else None, size_bytes, 0)
        self._store[key] = entry
        self._bytes = new_bytes
        self._index_expiry(key, entry)

    def put(self, namespace: str, key: str, value: object, ttl_seconds: int | None = None) -> None:
        with self._lock:
            self._purge_expired()
            ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
            self._write((namespace, key), value, ttl)

    def get(self, namespace: str, key: str) -> object | None:
        with self._lock:
            self._purge_expired()
            k = (namespace, key)
            entry = self._store.get(k)
            if entry is None:
                return None
            if entry.expires_at is not None and entry.expires_at <= self._now():
                self.delete(namespace, key)
                return None
            return entry.value

    def delete(self, namespace: str, key: str) -> None:
        with self._lock:
            entry = self._store.pop((namespace, key), None)
            if entry is not None:
                self._bytes -= entry.size_bytes
                self._compact_expiry_heap()

    def expire(self, namespace: str, key: str, ttl_seconds: int) -> None:
        with self._lock:
            self._purge_expired()
            k = (namespace, key)
            entry = self._store.get(k)
            if entry is not None:
                if ttl_seconds <= 0:
                    self.delete(namespace, key)
                else:
                    entry.expires_at = self._now() + ttl_seconds
                    self._index_expiry(k, entry)

    def size(self) -> int:
        with self._lock:
            self._purge_expired()
            return len(self._store)

    def ensure_capacity(self, extra_bytes: int) -> None:
        with self._lock:
            self._purge_expired()
            if len(self._store) >= self._max_entries:
                raise VaultCapacityError("vault at max entries")
            if self._bytes + extra_bytes > self._max_bytes:
                raise VaultCapacityError("vault at max bytes")

    def atomic_claim(
        self, namespace: str, key: str, record: object, ttl_seconds: int
    ) -> tuple[bool, object]:
        with self._lock:
            self._purge_expired()
            k = (namespace, key)
            existing = self._store.get(k)
            if existing is not None:
                return False, existing.value
            self._write(k, record, ttl_seconds)
            return True, record

    def atomic_commit(
        self, namespace: str, key: str, record: object, owner_id: str, ttl_seconds: int
    ) -> bool:
        with self._lock:
            self._purge_expired()
            k = (namespace, key)
            existing = self._store.get(k)
            if existing is None:
                return False
            current = existing.value
            if not isinstance(current, dict) or current.get("lease_owner") != owner_id:
                return False
            self._write(k, record, ttl_seconds)
            return True

    def atomic_release(self, namespace: str, key: str, owner_id: str) -> bool:
        with self._lock:
            self._purge_expired()
            existing = self._store.get((namespace, key))
            if existing is None:
                return False
            current = existing.value
            if not isinstance(current, dict) or current.get("lease_owner") != owner_id:
                return False
            self.delete(namespace, key)
            return True

    def atomic_reclaim(
        self, namespace: str, key: str, record: object, ttl_seconds: int, now: float
    ) -> bool:
        with self._lock:
            self._purge_expired()
            k = (namespace, key)
            existing = self._store.get(k)
            if existing is None:
                return False
            current = existing.value
            if not isinstance(current, dict):
                return False
            if current.get("state") != "PROCESSING":
                return False
            lease_exp = current.get("lease_expires_at")
            if lease_exp is None or lease_exp > now:
                return False
            # `now` is the lifecycle wall clock. Storage TTL uses monotonic time.
            self._write(k, record, ttl_seconds)
            return True
