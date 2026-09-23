"""Normalized shadow copy with reverse mapping to original intervals.

The original text is immutable. We build a normalized string for case-insensitive
search and controlled normalization (NBSP, dashes, whitespace) while keeping a
mapping from each normalized character position back to the original [start, end)
Unicode code-point interval.

Design: normalization is a per-character transform. Each normalized character maps
to exactly one original character interval. This keeps the reverse mapping simple
and lossless for the transforms we apply (case folding, NBSP->space, dash variants,
whitespace collapsing is NOT done here to preserve 1:1 mapping — detectors handle
flexible separators themselves).
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# Characters we normalize to a plain space (1:1, single original char each).
_NBSP = "\u00a0"
_NARROW_NBSP = "\u202f"
_FIGURE_SPACE = "\u2007"
_THIN_SPACE = "\u2009"
_HAIR_SPACE = "\u200a"
_EM_SPACE = "\u2003"
_EN_SPACE = "\u2002"

_SPACE_VARIANTS = {
    _NBSP: " ",
    _NARROW_NBSP: " ",
    _FIGURE_SPACE: " ",
    _THIN_SPACE: " ",
    _HAIR_SPACE: " ",
    _EM_SPACE: " ",
    _EN_SPACE: " ",
}

# Dash variants normalized to ASCII hyphen (1:1).
_DASH_VARIANTS = {
    "\u2010": "-",  # hyphen
    "\u2011": "-",  # non-breaking hyphen
    "\u2012": "-",  # figure dash
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2212": "-",  # minus sign
}


@dataclass(frozen=True)
class NormalizedText:
    """Normalized text plus reverse mapping to original intervals."""

    original: str
    normalized: str
    # For each normalized char index -> (orig_start, orig_end) interval.
    orig_intervals: tuple[tuple[int, int], ...]

    def __len__(self) -> int:
        return len(self.normalized)

    def orig_interval_for(self, norm_start: int, norm_end: int) -> tuple[int, int]:
        """Map a normalized [start, end) interval back to original [start, end)."""
        if norm_start < 0 or norm_end > len(self.normalized) or norm_start > norm_end:
            raise ValueError(
                f"normalized interval out of range: [{norm_start},{norm_end}) "
                f"len={len(self.normalized)}"
            )
        if norm_start == norm_end:
            return (norm_start, norm_start)
        first = self.orig_intervals[norm_start]
        last = self.orig_intervals[norm_end - 1]
        return (first[0], last[1])

    def find(self, needle: str, start: int = 0) -> int:
        """Case-insensitive search for needle in normalized text, returns norm index."""
        return self.normalized.lower().find(needle.lower(), start)


def normalize(text: str) -> NormalizedText:
    """Build a normalized shadow copy with reverse mapping.

    Applies: case folding (via .lower() at search time, not here), NBSP/dash
    variants -> plain space/hyphen. Each normalized char maps to one original char.
    """
    out_chars: list[str] = []
    intervals: list[tuple[int, int]] = []
    for i, ch in enumerate(text):
        mapped = _SPACE_VARIANTS.get(ch, _DASH_VARIANTS.get(ch, ch))
        out_chars.append(mapped)
        intervals.append((i, i + 1))
    return NormalizedText(
        original=text,
        normalized="".join(out_chars),
        orig_intervals=tuple(intervals),
    )
