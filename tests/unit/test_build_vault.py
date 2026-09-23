"""Tests for build_vault() config guards (R34, R49, section 11.3).

- PII_VAULT_BACKEND=memory + PII_WORKERS>1 -> RuntimeError (no split-brain).
- PII_VAULT_BACKEND=redis + missing PII_VAULT_KEY -> RuntimeError (no silent
  disabling of encryption).
- PII_VAULT_BACKEND=redis + valid key -> RedisVault.
- PII_VAULT_BACKEND=memory + PII_WORKERS=1 -> MemoryVault.
"""
from __future__ import annotations

import base64
import os

import pytest

from app.main import build_vault
from app.vault.memory import MemoryVault
from app.vault.redis import RedisVault

_AEAD_KEY = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


class TestMultiworkerMemoryGuard:
    def test_memory_with_multiple_workers_raises(self, monkeypatch):
        monkeypatch.setenv("PII_VAULT_BACKEND", "memory")
        monkeypatch.setenv("PII_WORKERS", "2")
        with pytest.raises(RuntimeError):
            build_vault()

    def test_memory_with_single_worker_ok(self, monkeypatch):
        monkeypatch.setenv("PII_VAULT_BACKEND", "memory")
        monkeypatch.setenv("PII_WORKERS", "1")
        vault, fp, lc = build_vault()
        assert isinstance(vault, MemoryVault)

    def test_memory_default_single_worker_ok(self, monkeypatch):
        monkeypatch.setenv("PII_VAULT_BACKEND", "memory")
        monkeypatch.delenv("PII_WORKERS", raising=False)
        vault, fp, lc = build_vault()
        assert isinstance(vault, MemoryVault)


class TestRedisKeyGuard:
    def test_redis_missing_key_raises(self, monkeypatch):
        monkeypatch.setenv("PII_VAULT_BACKEND", "redis")
        monkeypatch.delenv("PII_VAULT_KEY", raising=False)
        with pytest.raises(RuntimeError):
            build_vault()

    def test_redis_with_key_returns_redis_vault(self, monkeypatch):
        monkeypatch.setenv("PII_VAULT_BACKEND", "redis")
        monkeypatch.setenv("PII_VAULT_KEY", _AEAD_KEY)
        monkeypatch.setenv("PII_VAULT_REDIS_URL", "redis://127.0.0.1:6399/0")
        vault, fp, lc = build_vault()
        assert isinstance(vault, RedisVault)
        vault.close()
