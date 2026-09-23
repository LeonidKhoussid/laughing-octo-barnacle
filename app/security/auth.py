"""API key verification for protected consumers.

X-System-ID alone is NOT authorization. Credentials determine the allowed profile.
"""
from __future__ import annotations

import hmac
import os

from app.policies.schema import ConsumerPolicy


class AuthError(Exception):
    """Raised on authentication/authorization failure."""


def _env_value(name: str) -> str:
    return os.environ.get(name, "")


def verify_api_key(policy: ConsumerPolicy, provided_key: str | None) -> bool:
    """Verify the provided API key against the consumer's configured env key."""
    if policy.authentication == "competition_exception":
        return True
    if policy.authentication != "api_key":
        return False
    if not policy.api_key_env:
        return False
    expected = _env_value(policy.api_key_env)
    if not expected:
        return False
    if not provided_key:
        return False
    return hmac.compare_digest(expected, provided_key)
