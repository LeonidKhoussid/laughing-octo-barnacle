"""Unit tests for the payload_id lifecycle state machine (MemoryVault)."""
import os

import pytest

from app.core.engine import Engine
from app.detectors.registry import DetectorRegistry
from app.policies.schema import ConsumerPolicy
from app.vault.fingerprint import Fingerprinter
from app.vault.lifecycle import (
    ConflictError,
    ContextMissingError,
    Lifecycle,
    RetryableError,
)
from app.vault.memory import MemoryVault

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "configs")


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


def _lifecycle(vault=None, **kw) -> Lifecycle:
    vault = vault or MemoryVault()
    return Lifecycle(
        vault,
        _engine(),
        Fingerprinter(b"test-fp-key-32-bytes-long!!"),
        "baseline-001",
        **kw,
    )


class TestMaskLifecycle:
    def test_mask_new_original_commits(self):
        lc = _lifecycle()
        out = lc.mask("ns", "id1", "email a@b.com", _policy())
        assert "a@b.com" not in out.masked_text
        assert out.replay is False
        assert out.context_id == "id1"

    def test_mask_replay_same_original_same_mask(self):
        lc = _lifecycle()
        p = _policy()
        out1 = lc.mask("ns", "id1", "email a@b.com", p)
        out2 = lc.mask("ns", "id1", "email a@b.com", p)
        assert out1.masked_text == out2.masked_text
        assert out1.mapping == out2.mapping
        assert out2.replay is True

    def test_unmask_restores_original(self):
        lc = _lifecycle()
        p = _policy()
        out = lc.mask("ns", "id1", "email a@b.com", p)
        original = lc.unmask("ns", "id1", out.masked_text, p)
        assert original == "email a@b.com"

    def test_unmask_replay_same_original(self):
        lc = _lifecycle()
        p = _policy()
        out = lc.mask("ns", "id1", "email a@b.com", p)
        assert lc.unmask("ns", "id1", out.masked_text, p) == "email a@b.com"
        assert lc.unmask("ns", "id1", out.masked_text, p) == "email a@b.com"

    def test_same_id_different_original_conflict(self):
        lc = _lifecycle()
        p = _policy()
        lc.mask("ns", "id1", "email a@b.com", p)
        with pytest.raises(ConflictError):
            lc.mask("ns", "id1", "email c@d.com", p)

    def test_original_equals_masked_no_pii(self):
        lc = _lifecycle()
        p = _policy()
        text = "Сегодня хорошая погода"
        out = lc.mask("ns", "id1", text, p)
        assert out.masked_text == text
        # Repeat of the same text (no PII) is valid, not a conflict.
        out2 = lc.mask("ns", "id1", text, p)
        assert out2.masked_text == text

    def test_expired_mapping_context_missing(self):
        vault = MemoryVault(default_ttl_seconds=1)
        lc = _lifecycle(vault)
        p = _policy()
        out = lc.mask("ns", "id1", "email a@b.com", p)
        vault.expire("ns", "id1", 0)
        with pytest.raises(ContextMissingError):
            lc.unmask("ns", "id1", out.masked_text, p)

    def test_initial_retention_separate_from_replay_grace(self):
        """A freshly masked record must keep the configured retention, not the
        shorter replay grace (section 11.4, R35)."""
        vault = MemoryVault(default_ttl_seconds=3600)
        lc = _lifecycle(vault, replay_grace_seconds=300, retention_seconds=3600)
        p = _policy()
        lc.mask("ns", "id1", "email a@b.com", p)
        entry = vault._store[("ns", "id1")]
        # Immediately after mask, the record must be retained for ~3600s, not ~300s.
        remaining = entry.expires_at - vault._now()
        assert remaining > 3000, f"expected ~3600s retention, got ~{remaining:.0f}s"

    def test_replay_grace_after_unmask(self):
        """After unmask, the record is kept for the shorter replay grace."""
        vault = MemoryVault(default_ttl_seconds=3600)
        lc = _lifecycle(vault, replay_grace_seconds=300, retention_seconds=3600)
        p = _policy()
        out = lc.mask("ns", "id1", "email a@b.com", p)
        lc.unmask("ns", "id1", out.masked_text, p)
        entry = vault._store[("ns", "id1")]
        remaining = entry.expires_at - vault._now()
        assert remaining <= 300, f"expected ~300s replay grace, got ~{remaining:.0f}s"

    def test_capacity_exhausted_retryable(self):
        vault = MemoryVault(max_entries=1)
        lc = _lifecycle(vault)
        p = _policy()
        lc.mask("ns", "id1", "email a@b.com", p)
        with pytest.raises(RetryableError):
            lc.mask("ns", "id2", "email c@d.com", p)

    def test_concurrent_mask_same_id_single_canonical(self):
        import threading

        lc = _lifecycle()
        p = _policy()
        results = []
        errors = []

        def worker():
            try:
                results.append(lc.mask("ns", "id1", "email a@b.com", p))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        # The committed record is canonical (READY before any unmask).
        record = lc._vault.get("ns", "id1")
        assert record["state"] == "READY"
        # All outcomes must round-trip to the same original.
        for out in results:
            assert lc.unmask("ns", "id1", out.masked_text, p) == "email a@b.com"

    def test_detect_types_limited(self):
        lc = _lifecycle()
        p = _policy(detect_types="EMAIL")
        text = "email a@b.com, телефон +7 912 345-67-89"
        out = lc.mask("ns", "id1", text, p)
        # Only EMAIL masked; phone left intact.
        assert "a@b.com" not in out.masked_text
        assert "+7 912 345-67-89" in out.masked_text
        assert lc.unmask("ns", "id1", out.masked_text, p) == text


class TestProcessRouting:
    def test_process_new_mask(self):
        lc = _lifecycle()
        p = _policy()
        out = lc.process("ns", "id1", "email a@b.com", p)
        assert "a@b.com" not in out.masked_text

    def test_process_mask_replay(self):
        lc = _lifecycle()
        p = _policy()
        out1 = lc.process("ns", "id1", "email a@b.com", p)
        out2 = lc.process("ns", "id1", "email a@b.com", p)
        assert out1.masked_text == out2.masked_text

    def test_process_demask(self):
        lc = _lifecycle()
        p = _policy()
        out = lc.process("ns", "id1", "email a@b.com", p)
        # Sending the masked text back restores the original.
        restored = lc.process("ns", "id1", out.masked_text, p)
        assert restored.masked_text == "email a@b.com"

    def test_process_conflict(self):
        lc = _lifecycle()
        p = _policy()
        lc.process("ns", "id1", "email a@b.com", p)
        with pytest.raises(ConflictError):
            lc.process("ns", "id1", "email c@d.com", p)
