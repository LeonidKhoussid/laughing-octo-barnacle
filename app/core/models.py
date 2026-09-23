"""Internal entity model for detection candidates.

Per section 7.1 of the master prompt. The repr deliberately does NOT include
original_value to avoid leaking PII into logs, traces, or debug output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Decision(str, Enum):
    MASK = "MASK"
    KEEP = "KEEP"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class Span:
    """A half-open [start, end) interval in original Unicode code-point coordinates."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < 0:
            raise ValueError(f"span coordinates must be non-negative: {self}")
        if self.end < self.start:
            raise ValueError(f"span end must be >= start: {self}")

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end

    def __repr__(self) -> str:
        return f"Span(start={self.start}, end={self.end})"


@dataclass(frozen=True)
class Signal:
    """A typed positive/negative signal contributing to a decision."""

    name: str
    value: float
    kind: str = "positive"


@dataclass(frozen=True)
class Link:
    """A relation to another candidate."""

    target_entity_id: str
    relation: str


@dataclass
class Detection:
    """A detection candidate produced by a detector.

    original_value is intentionally NOT stored here. Values are retrieved by
    interval from the original text only where needed.
    """

    entity_id: str
    category: str
    subtype: Optional[str] = None
    evidence_spans: tuple[Span, ...] = ()
    sensitive_spans: tuple[Span, ...] = ()
    signals: tuple[Signal, ...] = ()
    links: tuple[Link, ...] = ()
    detector_id: str = ""
    detector_version: str = ""
    score: float = 0.0
    decision: Decision = Decision.UNCERTAIN
    rule_id: str = ""

    def __repr__(self) -> str:
        # Safe repr: no original_value, no raw text.
        return (
            f"Detection(entity_id={self.entity_id!r}, category={self.category!r}, "
            f"subtype={self.subtype!r}, sensitive_spans={self.sensitive_spans!r}, "
            f"detector_id={self.detector_id!r}, score={self.score!r}, "
            f"decision={self.decision!r}, rule_id={self.rule_id!r})"
        )
