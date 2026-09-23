"""Merge overlapping/duplicate sensitive spans into a single non-overlapping plan.

Spans are in ORIGINAL coordinates. We validate start<end, within bounds, and
produce a non-overlapping plan. Invalid coordinates raise a processing error
rather than returning a success with raw text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from app.core.models import Detection, Span


class SpanResolutionError(Exception):
    """Raised when span coordinates are invalid."""


@dataclass
class ResolvedSpan:
    """A final non-overlapping sensitive span with provenance."""

    span: Span
    category: str
    detector_ids: list[str] = field(default_factory=list)
    rule_id: str = ""


def _validate_span(span: Span, text_len: int) -> None:
    if span.start < 0 or span.end > text_len:
        raise SpanResolutionError(
            f"span out of bounds: {span} text_len={text_len}"
        )
    if span.end <= span.start:
        raise SpanResolutionError(f"span must satisfy start<end: {span}")


def resolve_spans(
    detections: Iterable[Detection], text_len: int
) -> list[ResolvedSpan]:
    """Merge all sensitive spans into a non-overlapping plan in original coords.

    Overlapping spans are merged into a union interval. The category of the
    merged span is taken from the first (highest-priority) contributor.
    """
    raw: list[tuple[Span, str, str]] = []
    for det in detections:
        for span in det.sensitive_spans:
            _validate_span(span, text_len)
            raw.append((span, det.category, det.detector_id))

    if not raw:
        return []

    # Sort by start, then by longer span first (so a containing span wins).
    raw.sort(key=lambda item: (item[0].start, -item[0].length))

    merged: list[ResolvedSpan] = []
    for span, category, detector_id in raw:
        if not merged:
            merged.append(
                ResolvedSpan(span=span, category=category, detector_ids=[detector_id])
            )
            continue
        last = merged[-1]
        if span.start <= last.span.end:
            # Overlap or adjacency -> merge into union.
            new_end = max(last.span.end, span.end)
            last.span = Span(last.span.start, new_end)
            if detector_id not in last.detector_ids:
                last.detector_ids.append(detector_id)
        else:
            merged.append(
                ResolvedSpan(span=span, category=category, detector_ids=[detector_id])
            )

    return merged
