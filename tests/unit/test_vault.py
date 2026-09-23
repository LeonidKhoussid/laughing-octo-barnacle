"""MemoryVault and AEAD crypto tests."""
import base64
import os

import pytest

from app.vault.base import VaultCapacityError
from app.vault.crypto import AeadCipher, CryptoError
from app.vault.memory import MemoryVault


class TestMemoryVault:
    def test_put_get(self):
        v = MemoryVault()
        v.put("ns", "k", {"mapping": {"t": "v"}})
        assert v.get("ns", "k") == {"mapping": {"t": "v"}}

    def test_namespace_isolation(self):
        v = MemoryVault()
        v.put("ns1", "k", "v1")
        assert v.get("ns2", "k") is None

    def test_delete(self):
        v = MemoryVault()
        v.put("ns", "k", "v")
        v.delete("ns", "k")
        assert v.get("ns", "k") is None

    def test_ttl_expiry(self):
        v = MemoryVault(default_ttl_seconds=1)
        v.put("ns", "k", "v")
        assert v.get("ns", "k") == "v"
        v.expire("ns", "k", 0)
        assert v.get("ns", "k") is None

    def test_max_entries(self):
        v = MemoryVault(max_entries=2)
        v.put("ns", "a", "1")
        v.put("ns", "b", "2")
        with pytest.raises(VaultCapacityError):
            v.put("ns", "c", "3")

    def test_thread_safety(self):
        import threading

        v = MemoryVault()
        errors = []

        def worker(i):
            try:
                for j in range(100):
                    v.put("ns", f"{i}-{j}", j)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


class TestAeadCipher:
    def test_roundtrip(self):
        key = os.urandom(32)
        c = AeadCipher(key)
        ct = c.encrypt(b"secret", "ns", "rec")
        assert c.decrypt(ct, "ns", "rec") == b"secret"

    def test_wrong_context_fails(self):
        key = os.urandom(32)
        c = AeadCipher(key)
        ct = c.encrypt(b"secret", "ns1", "rec")
        with pytest.raises(CryptoError):
            c.decrypt(ct, "ns2", "rec")

    def test_tampered_ciphertext_fails(self):
        key = os.urandom(32)
        c = AeadCipher(key)
        ct = bytearray(c.encrypt(b"secret", "ns", "rec"))
        ct[-1] ^= 0xFF
        with pytest.raises(CryptoError):
            c.decrypt(bytes(ct), "ns", "rec")

    def test_unique_nonce(self):
        key = os.urandom(32)
        c = AeadCipher(key)
        ct1 = c.encrypt(b"secret", "ns", "rec")
        ct2 = c.encrypt(b"secret", "ns", "rec")
        assert ct1[:12] != ct2[:12]  # nonces differ

    def test_from_base64(self):
        key = base64.b64encode(os.urandom(32)).decode()
        c = AeadCipher.from_base64(key)
        ct = c.encrypt(b"x", "ns", "rec")
        assert c.decrypt(ct, "ns", "rec") == b"x"

    def test_invalid_key_length(self):
        with pytest.raises(CryptoError):
            AeadCipher(b"short")
