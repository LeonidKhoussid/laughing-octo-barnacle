"""Explicitly-marked demo provider (stub).

Receives only the masked text and returns a simple response with unchanged tokens.
It must NOT receive the Vault or original. Clearly labeled as a stub (not real
DeepSeek/AlfaGen).
"""
from __future__ import annotations

from app.providers.base import Provider


class StubProvider(Provider):
    """A fixed demo provider. NOT a real LLM."""

    provider_id = "stub"

    def complete(self, masked_text: str) -> str:
        # Returns a neutral response that preserves tokens unchanged.
        return (
            "Демонстрационный ответ (stub, не реальная модель): "
            "запрос получен, токены сохранены без изменений. "
            f"Текст: {masked_text}"
        )
