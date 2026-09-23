"""AEAD (AES-GCM) helper for encrypting stored mappings.

Uses the `cryptography` library. Correct unique nonce per encryption. Associated
data includes namespace, record id, and format version so ciphertext cannot be
moved between contexts. Keys come from env, NOT hardcoded.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT_VERSION = b"alfagen-vault-v1"


class CryptoError(Exception):
    """Raised on encryption/decryption failures."""


class AeadCipher:
    """AES-GCM encrypt/decrypt with AAD binding to namespace/record/version."""

    def __init__(self, key: bytes) -> None:
        if len(key) not in (16, 24, 32):
            raise CryptoError("AEAD key must be 16, 24, or 32 bytes")
        self._aesgcm = AESGCM(key)

    @staticmethod
    def from_base64(b64: str) -> "AeadCipher":
        try:
            key = base64.b64decode(b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise CryptoError("invalid base64 AEAD key") from exc
        return AeadCipher(key)

    def _aad(self, namespace: str, record_id: str) -> bytes:
        return b"|".join(
            [FORMAT_VERSION, namespace.encode("utf-8"), record_id.encode("utf-8")]
        )

    def encrypt(self, plaintext: bytes, namespace: str, record_id: str) -> bytes:
        nonce = os.urandom(12)  # unique nonce per encryption
        aad = self._aad(namespace, record_id)
        ct = self._aesgcm.encrypt(nonce, plaintext, aad)
        return nonce + ct

    def decrypt(self, ciphertext: bytes, namespace: str, record_id: str) -> bytes:
        if len(ciphertext) < 12:
            raise CryptoError("ciphertext too short")
        nonce = ciphertext[:12]
        ct = ciphertext[12:]
        aad = self._aad(namespace, record_id)
        try:
            return self._aesgcm.decrypt(nonce, ct, aad)
        except Exception as exc:  # noqa: BLE001
            raise CryptoError("decryption failed (integrity or wrong context)") from exc
