"""PASSPORT_ISSUER, PASSPORT_ISSUE_DATE, DRIVER_LICENSE detectors.

PASSPORT_ISSUER extracts the issuing authority (ОВД/УФМС/МВД + name) after a
"выдан"/"орган выдачи" trigger, limited to the end of the field/line/next
trigger. PASSPORT_ISSUE_DATE disambiguates from BIRTH_DATE by nearest context.
DRIVER_LICENSE matches the series+number structure of a driver's license.
"""
from __future__ import annotations

import re
import uuid

from app.core.models import Decision, Detection, Signal, Span
from app.detectors.base import Detector
from app.detectors.dates import (
    _ISO_DATE,
    _MONTHS,
    _MONTHS_ALT,
    _NUMERIC_DATE,
    _WORDS_DATE,
    _is_plausible_date,
    _parse_date_match,
)

_ISSUER_WORD = r"[А-ЯЁа-яё][А-ЯЁа-яё\-]*"
# Abbreviation token such as "г." in "УФМС России по г. Москве".
_ISSUER_ABBR = r"[А-ЯЁа-яё]{1,2}\."
_ISSUER_TOKEN = rf"(?:{_ISSUER_ABBR}|{_ISSUER_WORD})"
_PASSPORT_ISSUER_RE = re.compile(
    rf"(?i)(кем\s+выдан|орган\s+выдачи|выдан|выдано|выдана)"
    rf"[ \t:]*"
    rf"({_ISSUER_TOKEN}(?:[ \t]+{_ISSUER_TOKEN}){{0,6}})"
)

# Issuing authorities must start with one of these keywords (R: not any ORGANIZATION).
# Case-insensitive so lowercase "омвд" is accepted.
_ISSUER_KEYWORD = re.compile(
    r"^(ОВД|УФМС|МВД|ГУВД|УВД|ФМС|ОМВД|УМВД|ГУМВД|УФМС)\b",
    re.IGNORECASE,
)

# Stop extraction at the next field/trigger.
_ISSUER_STOP = re.compile(
    r"(?i)(дата\s+выдачи|код\s+подразделения|дата\s+рождения|место\s+рождения|"
    r"гражданство|пол\b|фамилия|имя\b|отчество|серия|номер)"
)

_ISSUE_CONTEXT = re.compile(r"(?i)(дата\s+выдачи|выдан|выдана|выдано)")
_BIRTH_CONTEXT = re.compile(
    r"(?i)(родился|родилась|дата\s+рождения|д\.?\s*р\.?|день\s+рождения)"
)

# Neutral/event context: a date near these words is an ordinary date, not an
# issue date (master prompt 8.4: "контекст события не равен рождению").
# Word boundaries prevent "порт" matching inside "паспорта".
_NEUTRAL_CONTEXT = re.compile(
    r"(?i)\b(встреча|срок\s+действия|год|заказ|порт|собрание|конференция|"
    r"концерт|праздник|событие|мероприятие)\b"
)

_DRIVER_LICENSE_RE = re.compile(
    r"(?i)(водительское\s+удостоверение|водительские\s+права|ВУ|удостоверение\s+водителя)"
    r"[ \t:]*"
    r"(\d{2}[ \t\-]*\d{2})[ \t\-]*(\d{6})"
)


class PassportIssuerDetector(Detector):
    detector_id = "passport_issuer"
    detector_version = "1.0.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        for m in _PASSPORT_ISSUER_RE.finditer(text):
            auth_start = m.start(2)
            authority = m.group(2)
            if not _ISSUER_KEYWORD.search(authority):
                continue
            stop_m = _ISSUER_STOP.search(authority)
            if stop_m:
                authority = authority[: stop_m.start()]
            authority = authority.rstrip()
            if not authority:
                continue
            auth_end = auth_start + len(authority)
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="PASSPORT_ISSUER",
                    evidence_spans=(Span(m.start(0), auth_end),),
                    sensitive_spans=(Span(auth_start, auth_end),),
                    signals=(Signal("issuer_context", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.85,
                    decision=Decision.MASK,
                    rule_id="passport-issuer-context",
                )
            )
        return out


class PassportIssueDateDetector(Detector):
    detector_id = "passport_issue_date"
    detector_version = "1.0.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        issue_contexts = list(_ISSUE_CONTEXT.finditer(text))
        birth_contexts = list(_BIRTH_CONTEXT.finditer(text))

        def _nearest(pos: int, contexts):
            best = None
            for c in contexts:
                dist = min(abs(pos - c.start()), abs(pos - c.end()))
                if best is None or dist < best[0]:
                    best = (dist, c)
            return best

        neutral_contexts = list(_NEUTRAL_CONTEXT.finditer(text))

        def _is_issue(pos: int) -> bool:
            issue = _nearest(pos, issue_contexts)
            birth = _nearest(pos, birth_contexts)
            neutral = _nearest(pos, neutral_contexts)
            if issue is None:
                return False
            # A closer neutral/event context means this is an ordinary date.
            if neutral is not None and neutral[0] < issue[0]:
                return False
            if birth is None:
                return True
            return issue[0] < birth[0]

        for m in _NUMERIC_DATE.finditer(text):
            parsed = _parse_date_match(m)
            if parsed is None:
                continue
            day, month, year = parsed
            if not _is_plausible_date(day, month, year):
                continue
            if not _is_issue(m.start()):
                continue
            start, end = m.start(), m.end()
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="PASSPORT_ISSUE_DATE",
                    evidence_spans=(Span(start, end),),
                    sensitive_spans=(Span(start, end),),
                    signals=(Signal("issue_context", 1.0), Signal("numeric_date", 1.0)),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.9,
                    decision=Decision.MASK,
                    rule_id="passport-issue-date-numeric",
                )
            )

        for m in _ISO_DATE.finditer(text):
            parsed = _parse_date_match(m)
            if parsed is None:
                continue
            day, month, year = parsed
            if not _is_plausible_date(day, month, year):
                continue
            if not _is_issue(m.start()):
                continue
            start, end = m.start(), m.end()
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="PASSPORT_ISSUE_DATE",
                    evidence_spans=(Span(start, end),),
                    sensitive_spans=(Span(start, end),),
                    signals=(Signal("issue_context", 1.0), Signal("iso_date", 1.0)),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.9,
                    decision=Decision.MASK,
                    rule_id="passport-issue-date-iso",
                )
            )

        for m in _WORDS_DATE.finditer(text):
            parsed = _parse_date_match(m)
            if parsed is None:
                continue
            day, month, year = parsed
            if not _is_plausible_date(day, month, year):
                continue
            if not _is_issue(m.start()):
                continue
            start, end = m.start(), m.end()
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="PASSPORT_ISSUE_DATE",
                    evidence_spans=(Span(start, end),),
                    sensitive_spans=(Span(start, end),),
                    signals=(Signal("issue_context", 1.0), Signal("words_date", 1.0)),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.9,
                    decision=Decision.MASK,
                    rule_id="passport-issue-date-words",
                )
            )
        return out


class DriverLicenseDetector(Detector):
    detector_id = "driver_license"
    detector_version = "1.0.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        for m in _DRIVER_LICENSE_RE.finditer(text):
            series_start = m.start(2)
            number_end = m.end(3)
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="DRIVER_LICENSE",
                    evidence_spans=(Span(m.start(0), m.end(0)),),
                    sensitive_spans=(Span(series_start, number_end),),
                    signals=(Signal("driver_license_context", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.9,
                    decision=Decision.MASK,
                    rule_id="driver-license-context",
                )
            )
        return out
