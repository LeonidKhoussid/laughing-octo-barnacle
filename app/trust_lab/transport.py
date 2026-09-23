"""Instrumented transport adapter that counts actual upstream calls.

The call count for Action C comes from this ACTUAL instrumented transport, not a
UI constant (section 17.2 Action C). It wraps the stub provider and counts every
complete() invocation.
"""
from __future__ import annotations

import threading

from app.providers.base import Provider


class TransportCounter:
    """Wraps a provider and counts actual upstream calls."""

    def __init__(self, provider: Provider) -> None:
        self._provider = provider
        self._lock = threading.Lock()
        self._calls = 0

    @property
    def provider_id(self) -> str:
        return self._provider.provider_id

    @property
    def calls(self) -> int:
        with self._lock:
            return self._calls

    def complete(self, masked_text: str) -> str:
        with self._lock:
            self._calls += 1
        return self._provider.complete(masked_text)
