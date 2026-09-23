"""Provider interface for the demo LLM egress."""
from __future__ import annotations

import abc


class ProviderError(Exception):
    """Base provider error."""


class Provider(abc.ABC):
    """A provider receives only masked text and returns a response."""

    provider_id: str = "base"

    @abc.abstractmethod
    def complete(self, masked_text: str) -> str:
        """Return a completion for the masked text. Must not receive Vault/original."""
