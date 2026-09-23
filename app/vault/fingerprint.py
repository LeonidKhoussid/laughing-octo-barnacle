"""Keyed HMAC fingerprint of original text.

Per rules R11.2/R49, plain public SHA hashes of low-entropy values are
enumerable. We use a keyed HMAC-SHA256 so the fingerprint is not reversible
without the key. The key comes from env (PII_VAULT_FINGERPRINT_KEY); if unset it
is derived from the AEAD key via HKDF so no extra secret is required.
"""
from __future__ import annotations

import hashlib
import hmac

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def derive_fingerprint_key(aead_key: bytes) -> bytes:
    """Derive a stable HMAC key from the AEAD key (HKDF-SHA256)."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"alfagen-fingerprint",
        info=b"pii-vault-fingerprint-v1",
    ).derive(aead_key)


class Fingerprinter:
    """Computes keyed HMAC-SHA256 fingerprints of original text."""

    def __init__(self, key: bytes) -> None:
        self._key = key

    def fingerprint(self, text: str) -> str:
        return hmac.new(self._key, text.encode("utf-8"), hashlib.sha256).hexdigest()
