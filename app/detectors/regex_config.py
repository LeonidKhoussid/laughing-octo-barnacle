"""Config-driven regex detector (C4: add a simple new data type without
rewriting the core).

A user can define a new sensitive data type purely in detectors.yaml:

    - id: custom_account
      type: regex
      category: ACCOUNT_NUMBER
      pattern: "\\b[A-Z]{2}\\d{10}\\b"
      context: "(?i)(счёт|счет|account)"
      enabled: true

The detector matches the pattern and, if a context marker is present within a
window, emits a Detection with the matched span as sensitive. This is the
smallest safe config/registry mechanism for extensibility (section 12.5, R42).
"""
from __future__ import annotations

import re
import uuid

from app.core.models import Decision, Detection, Signal, Span
from app.detectors.base import Detector


class RegexDetector(Detector):
    """A detector defined entirely by configuration (pattern + category)."""

    def __init__(
        self,
        detector_id: str,
        category: str,
        pattern: str,
        context: str | None = None,
        context_window: int = 40,
        flags: int = 0,
    ) -> None:
        self.detector_id = detector_id
        self.detector_version = "config-1.0.0"
        self._category = category
        self._pattern = re.compile(pattern, flags)
        self._context = re.compile(context, flags) if context else None
        self._context_window = context_window

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        contexts = list(self._context.finditer(text)) if self._context else []

        def near_context(pos: int) -> bool:
            if not contexts:
                return True
            for c in contexts:
                if c.start() - 10 <= pos <= c.end() + self._context_window:
                    return True
            return False

        for m in self._pattern.finditer(text):
            start, end = m.start(), m.end()
            if not near_context(start):
                continue
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category=self._category,
                    evidence_spans=(Span(start, end),),
                    sensitive_spans=(Span(start, end),),
                    signals=(Signal("config_regex", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.8,
                    decision=Decision.MASK,
                    rule_id=f"config-regex-{self.detector_id}",
                )
            )
        return out