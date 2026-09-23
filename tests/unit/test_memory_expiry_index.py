"""Expiry generations, capacity accounting and owner transitions in MemoryVault."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.vault.base import VaultCapacityError
from app.vault.memory import MemoryVault


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(MemoryVault, "_now", lambda self: now[0])
    return now


def test_extended_ttl_survives_old_expiry(clock):
    vault = MemoryVault()
    vault.put("a", "key", "value", 10)
    clock[0] = 105
    vault.expire("a", "key", 20)
    clock[0] = 110
    assert vault.get("a", "key") == "value"
    clock[0] = 125
    assert vault.get("a", "key") is None
    assert vault._bytes == 0


def test_overwrite_generation_and_namespace_isolation(clock):
    vault = MemoryVault(default_ttl_seconds=None)
    vault.put("a", "key", "old", 10)
    vault.put("a", "key", "same deadline", 10)
    vault.put("b", "key", "other namespace", 20)
    vault.put("a", "key", "no expiry")
    clock[0] = 110
    assert vault.get("a", "key") == "no expiry"
    assert vault.get("b", "key") == "other namespace"
    clock[0] = 120
    assert vault.get("b", "key") is None
    assert vault.get("a", "key") == "no expiry"


def test_deleted_then_recreated_key_survives_stale_heap_entry(clock):
    vault = MemoryVault()
    vault.put("a", "key", "old", 10)
    vault.delete("a", "key")
    vault.put("a", "key", "new", 20)
    clock[0] = 110
    assert vault.get("a", "key") == "new"
    assert vault._bytes == len("new")
    clock[0] = 120
    assert vault.size() == 0
    assert vault._bytes == 0


def test_shortened_ttl_and_nonexpiring_entry(clock):
    vault = MemoryVault(default_ttl_seconds=None)
    vault.put("a", "key", "value")
    vault.expire("a", "key", 20)
    vault.expire("a", "key", 5)
    clock[0] = 105
    assert vault.get("a", "key") is None
    assert vault._bytes == 0


def test_expire_cannot_resurrect_expired_record(clock):
    vault = MemoryVault()
    vault.put("a", "key", "value", 1)
    clock[0] = 101
    vault.expire("a", "key", 100)
    assert vault.get("a", "key") is None


def test_expired_records_free_entry_and_byte_capacity(clock):
    vault = MemoryVault(max_entries=2, max_bytes=6)
    vault.put("a", "one", "123", 1)
    vault.put("a", "two", "456", 20)
    clock[0] = 101
    vault.ensure_capacity(3)
    vault.put("b", "three", "789")
    assert vault.size() == 2
    assert vault._bytes == 6


def test_failed_put_replacement_preserves_value_bytes_and_ttl(clock):
    vault = MemoryVault(max_bytes=6)
    vault.put("a", "key", "1234", 10)
    original_expiry = vault._store[("a", "key")].expires_at
    with pytest.raises(VaultCapacityError):
        vault.put("a", "key", "too large", 100)
    assert vault.get("a", "key") == "1234"
    assert vault._bytes == 4
    assert vault._store[("a", "key")].expires_at == original_expiry
    vault.put("a", "fits", "56")
    with pytest.raises(VaultCapacityError):
        vault.put("a", "overflow", "7")
    assert vault._bytes == 6


def test_failed_commit_preserves_original_record(clock):
    original = {"lease_owner": "owner"}
    vault = MemoryVault(max_bytes=len(str(original)))
    assert vault.atomic_claim("a", "key", original, 10)[0]
    with pytest.raises(VaultCapacityError):
        vault.atomic_commit("a", "key", {**original, "extra": "too much"}, "owner", 100)
    assert vault.get("a", "key") == original
    assert vault._bytes == len(str(original))
    clock[0] = 110
    assert vault.get("a", "key") is None


def test_reclaim_uses_monotonic_ttl_and_rejects_stale_owner(clock):
    vault = MemoryVault()
    original = {"state": "PROCESSING", "lease_owner": "old", "lease_expires_at": 1_700_000_001}
    replacement = {"state": "PROCESSING", "lease_owner": "new", "lease_expires_at": 1_700_000_006}
    vault.atomic_claim("a", "key", original, 30)
    assert not vault.atomic_reclaim("a", "key", replacement, 5, 1_700_000_000)
    clock[0] = 105
    assert vault.atomic_reclaim("a", "key", replacement, 5, 1_700_000_001)
    assert not vault.atomic_commit("a", "key", {}, "old", 10)
    assert not vault.atomic_release("a", "key", "old")
    assert vault.get("a", "key") == replacement
    clock[0] = 110
    assert vault.get("a", "key") is None
    assert not vault.atomic_commit("a", "key", {}, "new", 10)


def test_commit_extends_claim_ttl_without_stale_eviction(clock):
    vault = MemoryVault()
    vault.atomic_claim("a", "key", {"lease_owner": "owner"}, 1)
    ready = {"lease_owner": "owner", "state": "READY"}
    assert vault.atomic_commit("a", "key", ready, "owner", 30)
    clock[0] = 101
    assert vault.get("a", "key") == ready
    clock[0] = 130
    assert vault.get("a", "key") is None


def test_expiry_heap_growth_bounded_during_overwrite_refresh_delete(clock):
    vault = MemoryVault()
    for i in range(2000):
        vault.put("a", "key", str(i), 100)
        vault.expire("a", "key", 200)
        vault.put("b", str(i), "deleted", 100)
        vault.delete("b", str(i))
        assert len(vault._expiry_heap) <= max(64, 2 * vault.size())
    assert vault.get("a", "key") == "1999"
    assert vault._bytes == 4
    clock[0] = 300
    assert vault.size() == 0
    assert vault._bytes == 0
    assert not vault._expiry_heap


def test_single_owner_under_concurrent_claim_commit_release(clock):
    vault = MemoryVault()
    barrier = Barrier(16)

    def attempt(i):
        owner = str(i)
        barrier.wait()
        claimed, _ = vault.atomic_claim("a", "key", {"lease_owner": owner}, 10)
        if claimed:
            assert vault.atomic_commit("a", "key", {"lease_owner": owner, "state": "READY"}, owner, 30)
        else:
            assert not vault.atomic_commit("a", "key", {}, owner, 30)
            assert not vault.atomic_release("a", "key", owner)
        return claimed, owner

    with ThreadPoolExecutor(max_workers=16) as executor:
        outcomes = list(executor.map(attempt, range(16)))
    winners = [owner for claimed, owner in outcomes if claimed]
    assert len(winners) == 1
    assert vault.get("a", "key") == {"lease_owner": winners[0], "state": "READY"}
    clock[0] = 110
    assert vault.get("a", "key") is not None
    clock[0] = 130
    assert vault.atomic_claim("a", "key", {"lease_owner": "next"}, 10)[0]
    assert not vault.atomic_release("a", "key", winners[0])
    assert vault.atomic_release("a", "key", "next")
    assert vault.size() == 0
    assert vault._bytes == 0
