"""A config-driven recognizer for a synthetic internal client ID (section 12.4).

This is a simple, bounded regex recognizer used to demonstrate adding a detector
via config without rewriting the core. It recognizes a signed synthetic internal
ID of the form "клиентский ID: <value>" where <value> is 8-12 alphanumeric
characters. The signature stays outside the sensitive span; only the value is
masked. It is NOT a legal/standardized document identifier.
"""
from __future__ import annotations

import re
import uuid

from app.core.models import Decision, Detection, Signal, Span
from app.detectors.base import Detector

_SYNTH_ID_RE = re.compile(
    r"(?i)(клиентский\s+id|клиентский\s+идентификатор)[\s:№]*([A-Z0-9]{8,12})"
)


class SyntheticClientIdDetector(Detector):
    """Recognizes a signed synthetic internal client ID."""

    detector_id = "synthetic_client_id"
    detector_version = "1.0.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        for m in _SYNTH_ID_RE.finditer(text):
            value_start, value_end = m.start(2), m.end(2)
            out.append(
                Detection(
                    entity_id=uuid.uuid4().hex,
                    category="SYNTHETIC_CLIENT_ID",
                    evidence_spans=(Span(m.start(1), m.end(1)),),
                    sensitive_spans=(Span(value_start, value_end),),
                    signals=(Signal("signed_internal_id", 1.0, "positive"),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.95,
                    decision=Decision.MASK,
                )
            )
        return out
