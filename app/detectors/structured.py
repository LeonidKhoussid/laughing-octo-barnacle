"""Regex-based structured detectors: EMAIL, PHONE, CARD, INN, PASSPORT, DEPARTMENT_CODE.

Evidence spans vs sensitive spans differ where appropriate. For example in
"паспорт серия 0318 номер 123456" only 0318 and 123456 are sensitive; the words
"паспорт/серия/номер" stay.
"""
from __future__ import annotations

import re
import uuid

from app.core.models import Decision, Detection, Signal, Span
from app.detectors.base import Detector

_EMAIL_RE = re.compile(
    # A terminal sentence dot is punctuation, but a dot followed by another
    # domain label is not a valid point to truncate the address.
    r"(?<![A-Za-z0-9._%+-])"
    r"([A-Za-z0-9._%+-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63})"
    r"(?![A-Za-z0-9_%+@-]|\.[A-Za-z0-9])"
)

# Phone: optional +7/8, optional area code in parens, groups of digits separated by
# spaces/dashes (including Unicode dashes). We capture the full phone including
# separators as sensitive. An optional extension ("доб. 321", "ext. 321",
# "добавочный 321") is included in the sensitive span (master prompt 8.4).
_DASH = r"[\s\-–—]"
_PHONE_EXT = r"(?:\s+(?:доб\.?|добавочный|ext\.?|extension)\s*\d{1,6})?"
_PHONE_RE = re.compile(
    rf"(?<!\d)(?:\+7|8|7){_DASH}*(?:\(\d{{3}}\)|\d{{3}}){_DASH}*\d{{3}}{_DASH}*\d{{2}}{_DASH}*\d{{2}}{_PHONE_EXT}(?!\d)"
)

# Card: 13-19 digits, optionally grouped by spaces/dashes/NBSP. Luhn is a signal.
_CARD_RE = re.compile(
    r"(?<!\d)(?:\d[ \-\u00a0]?){12,18}\d(?!\d)"
)

# INN: 10 (legal) or 12 (individual) digits. Checksum is a signal, not a gate.
_INN_RE = re.compile(r"(?<!\d)(\d{10}|\d{12})(?!\d)")

# Passport series (4 digits) and number (6 digits) with context "серия ... номер ..."
# OR a bare "паспорт:" label OR a bare series+number pair.
_PASSPORT_RE = re.compile(
    r"(?i)(?:серия|серии|серией)[\s:№]*(\d{2}[\s\-–—]?\d{2})[\s,;]*"
    r"(?:номер|№|номером|номеру)?[\s:№]*(\d{6})"
)
# Bare passport: "паспорт: 4503 123456" or "паспорт 4503 123456".
_PASSPORT_BARE_RE = re.compile(
    r"(?i)(?:паспорт|паспорта|паспортом)[\s:№]*"
    r"(\d{2}[\s\-–—]?\d{2})[\s\-–—]*(\d{6})"
)
# Bare series+number with no label at all (e.g. "4503 123456").
_PASSPORT_RAW_RE = re.compile(
    r"(?<!\d)(\d{2}[\s\-–—]?\d{2})[\s\-–—]+(\d{6})(?!\d)"
)

# Department code: "код подразделения" + 3-3 digits with optional dash/space/Unicode dash.
_DEPARTMENT_CODE_RE = re.compile(
    r"(?i)(код\s+подразделения|код\s+подразделения:)[\s:]*(\d{3})[\s\-–—]*(\d{3})"
)

# Order-id / non-PII numeric context that must suppress CARD/INN detection.
_ORDER_ID_CONTEXT = re.compile(
    r"(?i)(номер\s+заказа|заказ\s+№|заказ\s+номер|id\s+заказа|номер\s+договора|"
    r"номер\s+счёта|номер\s+счета|номер\s+операции|номер\s+транзакции|"
    r"номер\s+документа|номер\s+заявки|номер\s+обращения|номер\s+чека|"
    r"номер\s+квитанции|номер\s+платежа|номер\s+поручения|номер\s+счёта)"
)


def _luhn_valid(digits: str) -> bool:
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _inn_checksum_valid(inn: str) -> bool:
    if len(inn) == 10:
        weights = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        check = sum(int(inn[i]) * weights[i] for i in range(9)) % 11 % 10
        return check == int(inn[9])
    if len(inn) == 12:
        w1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        w2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        c1 = sum(int(inn[i]) * w1[i] for i in range(10)) % 11 % 10
        c2 = sum(int(inn[i]) * w2[i] for i in range(11)) % 11 % 10
        return c1 == int(inn[10]) and c2 == int(inn[11])
    return False


def _in_order_id_context(text: str, pos: int) -> bool:
    """True if a non-PII numeric label (order/document id) precedes the position."""
    for m in _ORDER_ID_CONTEXT.finditer(text):
        if m.end() <= pos <= m.end() + 40:
            return True
    return False


class EmailDetector(Detector):
    detector_id = "email"
    detector_version = "1.1.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        for m in _EMAIL_RE.finditer(text):
            start, end = m.start(1), m.end(1)
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="EMAIL",
                    evidence_spans=(Span(start, end),),
                    sensitive_spans=(Span(start, end),),
                    signals=(Signal("email_structure", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.95,
                    decision=Decision.MASK,
                    rule_id="email-structure",
                )
            )
        return out


class PhoneDetector(Detector):
    detector_id = "phone"
    detector_version = "1.0.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        for m in _PHONE_RE.finditer(text):
            start, end = m.start(), m.end()
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="PHONE",
                    evidence_spans=(Span(start, end),),
                    sensitive_spans=(Span(start, end),),
                    signals=(Signal("phone_format", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.9,
                    decision=Decision.MASK,
                    rule_id="phone-format",
                )
            )
        return out


class CardDetector(Detector):
    detector_id = "card"
    detector_version = "1.0.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        for m in _CARD_RE.finditer(text):
            raw = m.group(0)
            digits = re.sub(r"[ \-\u00a0]", "", raw)
            if not (13 <= len(digits) <= 19):
                continue
            # A number labeled as an order/document id is not a card (C3).
            if _in_order_id_context(text, m.start()):
                continue
            luhn = _luhn_valid(digits)
            signals = [Signal("card_digits", 1.0)]
            if luhn:
                signals.append(Signal("luhn_valid", 1.0))
            else:
                signals.append(Signal("luhn_invalid", -0.2))
            start, end = m.start(), m.end()
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="CARD",
                    evidence_spans=(Span(start, end),),
                    sensitive_spans=(Span(start, end),),
                    signals=tuple(signals),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.85 if luhn else 0.6,
                    decision=Decision.MASK,
                    rule_id="card-digits",
                )
            )
        return out


class InnDetector(Detector):
    detector_id = "inn"
    detector_version = "1.0.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        for m in _INN_RE.finditer(text):
            inn = m.group(1)
            # A number labeled as an order/document id is not an INN (C3).
            if _in_order_id_context(text, m.start()):
                continue
            valid = _inn_checksum_valid(inn)
            signals = [Signal("inn_length", 1.0)]
            if valid:
                signals.append(Signal("inn_checksum_valid", 1.0))
            else:
                signals.append(Signal("inn_checksum_invalid", -0.2))
            start, end = m.start(1), m.end(1)
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="INN",
                    evidence_spans=(Span(start, end),),
                    sensitive_spans=(Span(start, end),),
                    signals=tuple(signals),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.8 if valid else 0.55,
                    decision=Decision.MASK,
                    rule_id="inn-structure",
                )
            )
        return out


class PassportDetector(Detector):
    detector_id = "passport"
    detector_version = "1.0.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        # Form 1: "серия 0318 номер 123456" (explicit series/number labels).
        for m in _PASSPORT_RE.finditer(text):
            series_start = m.start(1)
            series_end = m.end(1)
            num_start = m.start(2)
            num_end = m.end(2)
            evidence = Span(m.start(0), m.end(0))
            sensitive = (Span(series_start, series_end), Span(num_start, num_end))
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="PASSPORT",
                    subtype="PASSPORT_SERIES_NUMBER",
                    evidence_spans=(evidence,),
                    sensitive_spans=sensitive,
                    signals=(Signal("passport_context", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.95,
                    decision=Decision.MASK,
                    rule_id="passport-series-number",
                )
            )
        # Form 2: "паспорт: 4503 123456" (bare label + series+number).
        for m in _PASSPORT_BARE_RE.finditer(text):
            series_start = m.start(1)
            series_end = m.end(1)
            num_start = m.start(2)
            num_end = m.end(2)
            evidence = Span(m.start(0), m.end(0))
            sensitive = (Span(series_start, series_end), Span(num_start, num_end))
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="PASSPORT",
                    subtype="PASSPORT_SERIES_NUMBER",
                    evidence_spans=(evidence,),
                    sensitive_spans=sensitive,
                    signals=(Signal("passport_context", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.9,
                    decision=Decision.MASK,
                    rule_id="passport-bare-label",
                )
            )
        # Form 3: bare "4503 123456" with no label (4+6 digit pattern).
        for m in _PASSPORT_RAW_RE.finditer(text):
            series_start = m.start(1)
            series_end = m.end(1)
            num_start = m.start(2)
            num_end = m.end(2)
            evidence = Span(m.start(0), m.end(0))
            sensitive = (Span(series_start, series_end), Span(num_start, num_end))
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="PASSPORT",
                    subtype="PASSPORT_SERIES_NUMBER",
                    evidence_spans=(evidence,),
                    sensitive_spans=sensitive,
                    signals=(Signal("passport_structure", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.7,
                    decision=Decision.MASK,
                    rule_id="passport-raw-series-number",
                )
            )
        return out


class DepartmentCodeDetector(Detector):
    detector_id = "department_code"
    detector_version = "1.0.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        for m in _DEPARTMENT_CODE_RE.finditer(text):
            code_start = m.start(2)
            code_end = m.end(3)
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="DEPARTMENT_CODE",
                    evidence_spans=(Span(m.start(0), m.end(0)),),
                    sensitive_spans=(Span(code_start, code_end),),
                    signals=(Signal("department_code_context", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.9,
                    decision=Decision.MASK,
                    rule_id="department-code-context",
                )
            )
        return out
