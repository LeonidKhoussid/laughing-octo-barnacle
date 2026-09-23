"""BIRTH_PLACE and CITIZENSHIP detectors.

Evidence spans cover the whole construction (trigger + value); sensitive spans
cover only the geographic place / country value, not the service words.
"""
from __future__ import annotations

import re
import uuid

from app.core.models import Decision, Detection, Signal, Span
from app.detectors.base import Detector
from app.detectors.person import has_public_subject
from app.detectors.dates import _ISO_DATE, _NUMERIC_DATE, _WORDS_DATE, _WORDS_DAY_DATE

# Reuse date syntax so narrative birth facts can contain both values:
# «родилась 21 июня 1991 года в Перми». A date cannot cross a sentence.
_BIRTH_DATE_VALUE = "|".join(pattern.pattern for pattern in (
    _ISO_DATE, _NUMERIC_DATE, _WORDS_DATE, _WORDS_DAY_DATE,
))
_BIRTH_PLACE_RE = re.compile(
    r"(?P<trigger>(?i:мест[оаеу]\s+рождения)|"
    r"(?i:родился|родилась)[ \t]+"
    rf"(?:(?i:{_BIRTH_DATE_VALUE})[ \t]*(?i:года|году|г\.)?[ \t]*,?[ \t]*)?"
    r"(?i:в|во))"
    r"[ \t:]*"
    r"(?:(?i:г\.)[ \t]*|(?i:городе|город|в)[ \t]+)?"
    r"(?P<place>[А-ЯЁа-яё][А-ЯЁа-яё\-]+(?:[ \t]+[А-ЯЁ][А-ЯЁа-яё\-]+)?)"
)

_CITIZENSHIP_RE = re.compile(
    r"(?i)(гражданство|гражданин|гражданка)"
    r"[ \t:]*"
    r"([А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+)?)"
)

class BirthPlaceDetector(Detector):
    detector_id = "birth_place"
    detector_version = "1.3.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        for m in _BIRTH_PLACE_RE.finditer(text):
            trigger = m.group("trigger").lower()
            # "Место рождения" is a form-field label: always a client's birth
            # place, even if a profession word appears earlier (e.g. "Клиент
            # работает архитектором. Место рождения: Москва.").
            if trigger.startswith("мест"):
                place_start = m.start("place")
                place_end = m.end("place")
                out.append(
                    Detection(
                        entity_id=str(uuid.uuid4()),
                        category="BIRTH_PLACE",
                        evidence_spans=(Span(m.start(0), m.end(0)),),
                        sensitive_spans=(Span(place_start, place_end),),
                        signals=(Signal("birth_place_context", 1.0),),
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        score=0.9,
                        decision=Decision.MASK,
                        rule_id="birth-place-context",
                    )
                )
                continue
            # Narrative birthplaces use the same local subject classification
            # as names; an unrelated poet in an earlier sentence is irrelevant.
            if has_public_subject(text, m.start()):
                continue
            place_start = m.start("place")
            place_end = m.end("place")
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="BIRTH_PLACE",
                    evidence_spans=(Span(m.start(0), m.end(0)),),
                    sensitive_spans=(Span(place_start, place_end),),
                    signals=(Signal("birth_place_context", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.9,
                    decision=Decision.MASK,
                    rule_id="birth-place-context",
                )
            )
        return out


class CitizenshipDetector(Detector):
    detector_id = "citizenship"
    detector_version = "1.0.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        for m in _CITIZENSHIP_RE.finditer(text):
            country_start = m.start(2)
            country_end = m.end(2)
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="CITIZENSHIP",
                    evidence_spans=(Span(m.start(0), m.end(0)),),
                    sensitive_spans=(Span(country_start, country_end),),
                    signals=(Signal("citizenship_context", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.9,
                    decision=Decision.MASK,
                    rule_id="citizenship-context",
                )
            )
        return out
