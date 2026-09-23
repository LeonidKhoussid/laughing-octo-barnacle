"""One-pass exact substitution of registered tokens back to original values.

No LLM, no recursion, no fuzzy guessing. Unknown/damaged/foreign tokens are not
revealed (policy/status). The mapping is a dict token -> original_value.
"""
from __future__ import annotations

from typing import Mapping


class UnknownTokenError(Exception):
    """Raised when a token-like string is not a registered token."""


def detokenize(text: str, mapping: Mapping[str, str]) -> str:
    """Replace registered tokens with their original values in one pass.

    Scans left to right. When a registered token is found at the current position,
    it is substituted. Unknown token-like strings are left untouched (not revealed).
    """
    if not mapping:
        return text

    # Build a lookup of token -> value, and find the longest token for greedy match.
    tokens = sorted(mapping.keys(), key=len, reverse=True)

    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        matched = False
        for tok in tokens:
            if text.startswith(tok, i):
                out.append(mapping[tok])
                i += len(tok)
                matched = True
                break
        if not matched:
            out.append(text[i])
            i += 1
    return "".join(out)
