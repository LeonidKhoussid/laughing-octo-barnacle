"""Payload_id lifecycle state machine (master prompt section 6.2 / 6.3).

Coordinates the (namespace, payload_id) record across concurrent workers without
holding a global lock over detection. The critical section is only the per-key
state transition, performed atomically by the Vault (Redis Lua scripts or the
MemoryVault lock). NLP (masking) runs outside the critical section.

States:
    PROCESSING  a worker claimed the ID and is masking (has a lease)
    READY       the full record (masked_text + mapping) is committed
    RESTORED    unmask was served; mapping retained for replay grace

Routing rules (section 6.2):
    ID absent + new original            -> mask, commit, return
    PROCESSING + same original          -> wait briefly, else retryable
    READY + same original               -> return the SAME mask (no new tokens)
    READY + exact masked text           -> deterministically restore original
    RESTORED + same masked text         -> return same original within retention
    ID exists + different text          -> conflict (no silent overwrite)
    expired/lost + restore attempt      -> context missing (no invented original)
    original == masked (no PII)         -> valid, no conflict
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from app.core.engine import Engine
from app.policies.schema import ConsumerPolicy
from app.vault.base import Vault, VaultCapacityError, VaultIntegrityError, VaultUnavailableError
from app.vault.fingerprint import Fingerprinter

STATE_PROCESSING = "PROCESSING"
STATE_READY = "READY"
STATE_RESTORED = "RESTORED"


class LifecycleError(Exception):
    """Base lifecycle error."""


class ConflictError(LifecycleError):
    """A different, unrelated text was submitted under an existing ID."""


class RetryableError(LifecycleError):
    """The operation can be retried (processing in progress or overload)."""


class ContextMissingError(LifecycleError):
    """The context is missing or expired; no original can be invented."""


class IntegrityError(LifecycleError):
    """Stored ciphertext failed integrity/context checks."""


@dataclass
class MaskOutcome:
    masked_text: str
    mapping: dict[str, str] = field(default_factory=dict)
    context_id: str = ""
    policy_version: str = ""
    replay: bool = False
    resolved_spans: list = field(default_factory=list)


class Lifecycle:
    """Orchestrates the payload_id state machine over a Vault."""

    def __init__(
        self,
        vault: Vault,
        engine: Engine,
        fingerprinter: Fingerprinter,
        policy_version: str,
        *,
        lease_ttl_seconds: int = 30,
        wait_seconds: float = 2.0,
        wait_poll_seconds: float = 0.05,
        replay_grace_seconds: int = 300,
        retention_seconds: int | None = None,
    ) -> None:
        self._vault = vault
        self._engine = engine
        self._fp = fingerprinter
        self._policy_version = policy_version
        self._lease_ttl = lease_ttl_seconds
        self._wait_seconds = wait_seconds
        self._wait_poll = wait_poll_seconds
        self._replay_grace = replay_grace_seconds
        # Initial retention for a freshly masked record. Defaults to the vault's
        # configured default TTL (e.g. 3600s), NOT the shorter replay grace.
        # This separates "how long a mask stays restorable" from "how long a
        # restored record is kept for replay" (section 11.4, R35).
        self._retention = retention_seconds if retention_seconds is not None else replay_grace_seconds

    # -- public API -----------------------------------------------------------

    def mask(
        self,
        namespace: str,
        payload_id: str,
        original: str,
        policy: ConsumerPolicy,
    ) -> MaskOutcome:
        """Mask `original` under (namespace, payload_id) with replay semantics."""
        fp = self._fp.fingerprint(original)
        owner = uuid.uuid4().hex
        now = time.time()
        processing = self._processing_record(fp, owner, now)

        # Check capacity BEFORE claiming so we never issue an irreversible mask
        # that cannot be stored (R35).
        try:
            self._vault.ensure_capacity(self._estimate_size(original))
        except VaultCapacityError:
            raise RetryableError("vault at capacity; retry later") from None
        except VaultUnavailableError:
            raise RetryableError("vault unavailable; retry later") from None

        try:
            claimed, existing = self._vault.atomic_claim(
                namespace, payload_id, processing, self._lease_ttl
            )
        except VaultCapacityError:
            raise RetryableError("vault at capacity; retry later") from None
        if not claimed:
            return self._handle_existing_mask(
                namespace, payload_id, existing, fp, original, policy, owner, now
            )

        # We own the lease.
        try:
            result = self._engine.mask(
                original, namespace, payload_id,
                allowed_categories=policy.detect_types_set(), policy=policy,
            )
            ready = self._ready_record(fp, result.masked_text, result.token_mapping, owner, now)
            ok = self._vault.atomic_commit(
                namespace, payload_id, ready, owner, self._retention
            )
            if not ok:
                # Another worker took over the lease; return their committed result.
                return self._read_ready_or_retry(namespace, payload_id, fp, original)
            return MaskOutcome(
                masked_text=result.masked_text,
                mapping=result.token_mapping,
                context_id=payload_id,
                policy_version=self._policy_version,
                replay=False,
                resolved_spans=result.resolved_spans,
            )
        except LifecycleError:
            self._vault.atomic_release(namespace, payload_id, owner)
            raise
        except VaultUnavailableError:
            self._vault.atomic_release(namespace, payload_id, owner)
            raise RetryableError("vault unavailable; retry later") from None
        except Exception:
            self._vault.atomic_release(namespace, payload_id, owner)
            raise

    def unmask(
        self,
        namespace: str,
        payload_id: str,
        masked_text: str,
        policy: ConsumerPolicy,
    ) -> str:
        """Restore the exact original from masked text under (namespace, payload_id)."""
        try:
            record = self._vault.get(namespace, payload_id)
        except VaultUnavailableError:
            raise RetryableError("vault unavailable; retry later") from None
        if record is None:
            raise ContextMissingError("context not found or expired")
        if record.get("state") not in (STATE_READY, STATE_RESTORED):
            raise RetryableError("record still processing; retry later")
        if record.get("masked_text") != masked_text:
            raise ConflictError("text does not match the record for this ID")
        mapping = record.get("mapping") or {}
        original = self._engine.unmask(masked_text, mapping)
        # Mark RESTORED but keep the mapping for replay grace (R32).
        self._mark_restored(namespace, payload_id, record)
        return original

    def restore_tokens(
        self,
        namespace: str,
        payload_id: str,
        response_text: str,
        policy: ConsumerPolicy,
    ) -> str:
        """Restore authorized tokens inside a CHANGED provider response.

        The provider may prefix or reword its output, so the response text is not
        expected to equal the stored masked_text. This performs EXACT token
        substitution: every registered token found in the response is replaced
        with its original value. It is NOT fuzzy, recursive, or LLM-based (R25).
        Unknown/damaged tokens are left untouched.
        """
        try:
            record = self._vault.get(namespace, payload_id)
        except VaultUnavailableError:
            raise RetryableError("vault unavailable; retry later") from None
        if record is None:
            raise ContextMissingError("context not found or expired")
        if record.get("state") not in (STATE_READY, STATE_RESTORED):
            raise RetryableError("record still processing; retry later")
        mapping = record.get("mapping") or {}
        restored = self._engine.unmask(response_text, mapping)
        # Keep the mapping for replay grace (R32).
        self._mark_restored(namespace, payload_id, record)
        return restored

    def process(
        self,
        namespace: str,
        payload_id: str,
        text: str,
        policy: ConsumerPolicy,
    ) -> MaskOutcome:
        """Route a single-endpoint request between mask and unmask (autocheck).

        If a READY/RESTORED record exists and the incoming text equals the stored
        masked_text, this is a demask (restore). If it matches the original
        fingerprint, it is a mask replay. Otherwise it is a new mask.
        """
        try:
            record = self._vault.get(namespace, payload_id)
        except VaultUnavailableError:
            raise RetryableError("vault unavailable; retry later") from None
        if record is not None and record.get("state") in (STATE_READY, STATE_RESTORED):
            if record.get("masked_text") == text:
                original = self._engine.unmask(text, record.get("mapping") or {})
                self._mark_restored(namespace, payload_id, record)
                return MaskOutcome(
                    masked_text=original,
                    mapping=record.get("mapping") or {},
                    context_id=payload_id,
                    policy_version=self._policy_version,
                    replay=True,
                )
            if self._fp.fingerprint(text) == record.get("fingerprint"):
                return MaskOutcome(
                    masked_text=record["masked_text"],
                    mapping=record.get("mapping") or {},
                    context_id=payload_id,
                    policy_version=self._policy_version,
                    replay=True,
                )
            # Different text under an existing ID -> conflict.
            raise ConflictError("text does not match the record for this ID")
        return self.mask(namespace, payload_id, text, policy)

    # -- record builders ------------------------------------------------------

    def _processing_record(self, fp: str, owner: str, now: float) -> dict:
        return {
            "state": STATE_PROCESSING,
            "policy_version": self._policy_version,
            "fingerprint": fp,
            "masked_text": "",
            "mapping": {},
            "created_at": now,
            "updated_at": now,
            "lease_owner": owner,
            "lease_expires_at": now + self._lease_ttl,
        }

    def _ready_record(
        self, fp: str, masked_text: str, mapping: dict, owner: str, now: float
    ) -> dict:
        return {
            "state": STATE_READY,
            "policy_version": self._policy_version,
            "fingerprint": fp,
            "masked_text": masked_text,
            "mapping": mapping,
            "created_at": now,
            "updated_at": now,
            "lease_owner": owner,
            "lease_expires_at": now + self._lease_ttl,
        }

    # -- helpers --------------------------------------------------------------

    def _handle_existing_mask(
        self,
        namespace: str,
        payload_id: str,
        existing: dict | None,
        fp: str,
        original: str,
        policy: ConsumerPolicy,
        owner: str,
        now: float,
    ) -> MaskOutcome:
        if existing is None:
            # Record vanished between claim and read; retry as a fresh mask.
            return self.mask(namespace, payload_id, original, policy)

        state = existing.get("state")
        if state == STATE_PROCESSING:
            if existing.get("fingerprint") == fp:
                # Same original being masked by another worker.
                if self._lease_expired(existing, now):
                    # Stale lease -> reclaim and mask ourselves.
                    if self._vault.atomic_reclaim(
                        namespace, payload_id, self._processing_record(fp, owner, now),
                        self._lease_ttl, now,
                    ):
                        return self._mask_as_owner(
                            namespace, payload_id, fp, original, policy, owner, now
                        )
                return self._wait_for_ready(namespace, payload_id, fp, original, policy)
            raise ConflictError("a different text is already being processed for this ID")

        if state in (STATE_READY, STATE_RESTORED):
            if existing.get("fingerprint") == fp:
                # Mask replay of the same original -> same mask, no new tokens.
                return MaskOutcome(
                    masked_text=existing["masked_text"],
                    mapping=existing.get("mapping") or {},
                    context_id=payload_id,
                    policy_version=self._policy_version,
                    replay=True,
                )
            if existing.get("masked_text") == original:
                # original == masked (no PII) -> valid, return the same text.
                return MaskOutcome(
                    masked_text=original,
                    mapping=existing.get("mapping") or {},
                    context_id=payload_id,
                    policy_version=self._policy_version,
                    replay=True,
                )
            raise ConflictError("text does not match the record for this ID")

        raise ConflictError("unexpected record state")

    def _mask_as_owner(
        self,
        namespace: str,
        payload_id: str,
        fp: str,
        original: str,
        policy: ConsumerPolicy,
        owner: str,
        now: float,
    ) -> MaskOutcome:
        try:
            result = self._engine.mask(
                original, namespace, payload_id,
                allowed_categories=policy.detect_types_set(), policy=policy,
            )
            ready = self._ready_record(fp, result.masked_text, result.token_mapping, owner, now)
            ok = self._vault.atomic_commit(
                namespace, payload_id, ready, owner, self._retention
            )
            if not ok:
                return self._read_ready_or_retry(namespace, payload_id, fp, original)
            return MaskOutcome(
                masked_text=result.masked_text,
                mapping=result.token_mapping,
                context_id=payload_id,
                policy_version=self._policy_version,
                replay=False,
                resolved_spans=result.resolved_spans,
            )
        except LifecycleError:
            self._vault.atomic_release(namespace, payload_id, owner)
            raise
        except VaultUnavailableError:
            self._vault.atomic_release(namespace, payload_id, owner)
            raise RetryableError("vault unavailable; retry later") from None
        except Exception:
            self._vault.atomic_release(namespace, payload_id, owner)
            raise

    def _wait_for_ready(
        self,
        namespace: str,
        payload_id: str,
        fp: str,
        original: str,
        policy: ConsumerPolicy,
    ) -> MaskOutcome:
        deadline = time.time() + self._wait_seconds
        while time.time() < deadline:
            time.sleep(self._wait_poll)
            record = self._vault.get(namespace, payload_id)
            if record is None:
                return self.mask(namespace, payload_id, original, policy)
            if record.get("state") in (STATE_READY, STATE_RESTORED):
                if record.get("fingerprint") == fp:
                    return MaskOutcome(
                        masked_text=record["masked_text"],
                        mapping=record.get("mapping") or {},
                        context_id=payload_id,
                        policy_version=self._policy_version,
                        replay=True,
                    )
                if record.get("masked_text") == original:
                    return MaskOutcome(
                        masked_text=original,
                        mapping=record.get("mapping") or {},
                        context_id=payload_id,
                        policy_version=self._policy_version,
                        replay=True,
                    )
                raise ConflictError("text does not match the record for this ID")
        raise RetryableError("masking still in progress; retry later")

    def _read_ready_or_retry(
        self, namespace: str, payload_id: str, fp: str, original: str
    ) -> MaskOutcome:
        try:
            record = self._vault.get(namespace, payload_id)
        except VaultUnavailableError:
            raise RetryableError("vault unavailable; retry later") from None
        if record is not None and record.get("state") in (STATE_READY, STATE_RESTORED):
            if record.get("fingerprint") == fp:
                return MaskOutcome(
                    masked_text=record["masked_text"],
                    mapping=record.get("mapping") or {},
                    context_id=payload_id,
                    policy_version=self._policy_version,
                    replay=True,
                )
            if record.get("masked_text") == original:
                return MaskOutcome(
                    masked_text=original,
                    mapping=record.get("mapping") or {},
                    context_id=payload_id,
                    policy_version=self._policy_version,
                    replay=True,
                )
        raise RetryableError("masking in progress; retry later")

    def _mark_restored(self, namespace: str, payload_id: str, record: dict) -> None:
        record["state"] = STATE_RESTORED
        record["updated_at"] = time.time()
        # Keep the mapping; refresh TTL to replay grace (R32).
        try:
            self._vault.put(namespace, payload_id, record, self._replay_grace)
        except Exception:  # noqa: BLE001
            # Best-effort; the record still exists with its original TTL.
            pass

    @staticmethod
    def _lease_expired(record: dict, now: float) -> bool:
        lease_exp = record.get("lease_expires_at")
        return lease_exp is None or lease_exp <= now

    @staticmethod
    def _estimate_size(original: str) -> int:
        return len(original.encode("utf-8")) + 512
