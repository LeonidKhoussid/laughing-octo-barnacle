"""Vault interface, namespace-scoped."""
from __future__ import annotations

import abc
from typing import Any


class VaultError(Exception):
    """Base vault error."""


class VaultCapacityError(VaultError):
    """Raised when the vault is at capacity."""


class VaultIntegrityError(VaultError):
    """Raised when stored ciphertext fails integrity/context checks."""


class VaultUnavailableError(VaultError):
    """Raised when the backing store is unreachable (e.g. Redis down)."""


class Vault(abc.ABC):
    """Namespace-scoped key-value store for reversible mappings.

    Implementations must be thread-safe and, for multiworker deployments, backed
    by a shared store (e.g. Redis). The atomic_* primitives coordinate the
    payload_id lifecycle (claim -> mask -> commit / release) without holding a
    global lock over detection: the critical section is only the per-key state
    transition.
    """

    @abc.abstractmethod
    def put(self, namespace: str, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Store a value under (namespace, key)."""

    @abc.abstractmethod
    def get(self, namespace: str, key: str) -> Any | None:
        """Retrieve a value; returns None if missing or expired."""

    @abc.abstractmethod
    def delete(self, namespace: str, key: str) -> None:
        """Delete a value."""

    @abc.abstractmethod
    def expire(self, namespace: str, key: str, ttl_seconds: int) -> None:
        """Set/refresh TTL on a value."""

    @abc.abstractmethod
    def size(self) -> int:
        """Number of live entries across all namespaces."""

    @abc.abstractmethod
    def ensure_capacity(self, extra_bytes: int) -> None:
        """Raise VaultCapacityError if adding extra_bytes would exceed limits."""

    @abc.abstractmethod
    def atomic_claim(
        self, namespace: str, key: str, record: Any, ttl_seconds: int
    ) -> tuple[bool, Any]:
        """Atomically create the record if absent.

        Returns (True, record) if this caller created it, or (False, existing)
        if a record already exists for (namespace, key).
        """

    @abc.abstractmethod
    def atomic_reclaim(
        self, namespace: str, key: str, record: Any, ttl_seconds: int, now: float
    ) -> bool:
        """Atomically replace a stale PROCESSING record whose lease has expired.

        Returns True if reclaimed, False otherwise.
        """

    @abc.abstractmethod
    def atomic_commit(
        self, namespace: str, key: str, record: Any, owner_id: str, ttl_seconds: int
    ) -> bool:
        """Atomically replace the record only if its lease owner matches.

        Returns True on success, False if the owner no longer holds the lease.
        """

    @abc.abstractmethod
    def atomic_release(self, namespace: str, key: str, owner_id: str) -> bool:
        """Atomically delete the record only if its lease owner matches.

        Returns True if deleted, False if the owner no longer holds the lease.
        """
