"""Opaque token generation.

Format: ⟦PII:CATEGORY:<opaque-id>⟧ where opaque-id is cryptographically random
with >=128 bits of randomness. Tokens are scoped to a context (namespace +
context_id) so different consumers/contexts never share a global token.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field

_OPEN = "\u27e6"  # ⟦
_CLOSE = "\u27e7"  # ⟧


@dataclass(frozen=True)
class TokenContext:
    """Scoping for token generation: namespace + context id."""

    namespace: str
    context_id: str


@dataclass
class TokenGenerator:
    """Generates opaque, context-scoped tokens."""

    context: TokenContext
    # Map from (category, original_value) -> token for stable tokens within a context.
    _cache: dict[tuple[str, str], str] = field(default_factory=dict, init=False)

    def _make_token(self, category: str) -> str:
        opaque = secrets.token_urlsafe(16)  # 128 bits of randomness
        return f"{_OPEN}PII:{category}:{opaque}{_CLOSE}"

    def token_for(self, category: str, original_value: str) -> str:
        """Return a stable token for (category, value) within this context.

        The same exact value in one context gets one stable token. Different
        contexts use different TokenGenerator instances, so no global reuse.
        """
        key = (category, original_value)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        token = self._make_token(category)
        self._cache[key] = token
        return token

    def ensure_not_in_text(self, token: str, original: str) -> str:
        """Regenerate the token until it does not appear in the original text."""
        while token in original:
            token = self._make_token(token.split(":")[1])
        return token
