"""BIRTH_DATE detector: numeric and words, with context "родился"/"дата рождения"/"д.р."."""
from __future__ import annotations

from bisect import bisect_left
import re
import uuid

from app.core.models import Decision, Detection, Signal, Span
from app.detectors.base import Detector
from app.detectors.person import has_public_subject

_BIRTH_CONTEXT = re.compile(
    r"(?i)(родился|родилась|дат(?:а|у|ы|е|ой)\s+рождения|д\.\s*р\.|день\s+рождения)"
)

# Numeric date: DD.MM.YYYY or DD/MM/YYYY or DD-MM-YYYY, optionally with context.
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})\b")
# ISO date: YYYY-MM-DD (year first).
_ISO_DATE = re.compile(r"\b(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})\b")

# Words date: "12 апреля 1990" or "12 апреля 1990 года". The "года" suffix is
# captured separately so it is NOT part of the sensitive span (R13).
_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_MONTHS_ALT = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)
_MONTH_PATTERN = "|".join(_MONTHS + _MONTHS_ALT)
# Case-insensitive month matching (uppercase "АПРЕЛЯ" too).
_WORDS_DATE = re.compile(
    rf"\b(\d{{1,2}})\s+({_MONTH_PATTERN})\s+(\d{{4}})(?:\s+года)?\b",
    re.IGNORECASE,
)

# Written day: "двенадцатого апреля 1990" (day as a Russian ordinal word).
_DAY_WORDS = (
    "первого", "второго", "третьего", "четвертого", "четвёртого", "пятого",
    "шестого", "седьмого", "восьмого", "девятого", "десятого", "одиннадцатого",
    "двенадцатого", "тринадцатого", "четырнадцатого", "пятнадцатого",
    "шестнадцатого", "семнадцатого", "восемнадцатого", "девятнадцатого",
    "двадцатого", "двадцать", "тридцатого", "тридцать",
)
# Compound written days: "двадцать третьего", "тридцать первого", etc.
_DAY_TENS = ("двадцать", "тридцать")
_DAY_UNITS = (
    "первого", "второго", "третьего", "четвертого", "четвёртого", "пятого",
    "шестого", "седьмого", "восьмого", "девятого",
)
_DAY_WORDS_PATTERN = "|".join(_DAY_WORDS)
# A day is either a single ordinal word OR a compound "tens + unit".
_DAY_FULL_PATTERN = (
    rf"(?:{_DAY_WORDS_PATTERN}|"
    rf"(?:{'|'.join(_DAY_TENS)})\s+(?:{'|'.join(_DAY_UNITS)}))"
)
_WORDS_DAY_DATE = re.compile(
    rf"\b({_DAY_FULL_PATTERN})\s+({_MONTH_PATTERN})\s+(\d{{4}})(?:\s+года)?\b",
    re.IGNORECASE,
)


def _is_plausible_date(day: int, month: int, year: int) -> bool:
    if not (1 <= month <= 12):
        return False
    if not (1900 <= year <= 2026):
        return False
    if day < 1 or day > 31:
        return False
    return True


def _month_index(word: str) -> int:
    """Return 1-12 for a Russian month word (case-insensitive), or 0."""
    low = word.lower()
    if low in _MONTHS:
        return _MONTHS.index(low) + 1
    if low in _MONTHS_ALT:
        return _MONTHS_ALT.index(low) + 1
    return 0


def _parse_date_match(m) -> tuple[int, int, int] | None:
    """Parse a date match into (day, month, year), handling all formats.

    Returns None if the match is not a recognizable date.
    """
    if m.re is _NUMERIC_DATE:
        # DD.MM.YYYY (day first). If month > 12, try MM.DD.YYYY (US).
        d1, d2, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d2 <= 12:
            return d1, d2, year
        if 1 <= d1 <= 12:
            return d2, d1, year
        return None
    if m.re is _ISO_DATE:
        # YYYY-MM-DD (year first).
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return day, month, year
    if m.re is _WORDS_DATE:
        day, month_word, year = int(m.group(1)), m.group(2), int(m.group(3))
        month = _month_index(month_word)
        if month == 0:
            return None
        return day, month, year
    if m.re is _WORDS_DAY_DATE:
        day_word, month_word, year = m.group(1), m.group(2), int(m.group(3))
        day = _day_word_to_number(day_word)
        month = _month_index(month_word)
        if day is None or month == 0:
            return None
        return day, month, year
    return None


def _day_word_to_number(word: str) -> int | None:
    """Convert a Russian ordinal day word (or compound like "двадцать третьего")
    to a number, or None."""
    low = word.lower().strip()
    mapping = {
        "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4,
        "четвёртого": 4, "пятого": 5, "шестого": 6, "седьмого": 7,
        "восьмого": 8, "девятого": 9, "десятого": 10, "одиннадцатого": 11,
        "двенадцатого": 12, "тринадцатого": 13, "четырнадцатого": 14,
        "пятнадцатого": 15, "шестнадцатого": 16, "семнадцатого": 17,
        "восемнадцатого": 18, "девятнадцатого": 19, "двадцатого": 20,
        "двадцать": 20, "тридцатого": 30, "тридцать": 30,
    }
    if low in mapping:
        return mapping[low]
    # Compound: "двадцать третьего" -> 23, "тридцать первого" -> 31.
    parts = low.split()
    if len(parts) == 2 and parts[0] in ("двадцать", "тридцать"):
        tens = 20 if parts[0] == "двадцать" else 30
        unit = mapping.get(parts[1])
        if unit is not None and 1 <= unit <= 9:
            return tens + unit
    return None


def _birth_date_bridge(text: str) -> bool:
    """Allow a name/form label between birth marker and value, not a new fact."""
    if len(text) > 120:
        return False
    # Initials are not sentence boundaries: «Дата рождения клиента Р. Э. ...».
    bridge = re.sub(r"(?<!\w)[А-ЯЁA-Z]\.[ \t]*", "", text)
    if re.search(r"[.!?;\n\r]", bridge):
        return False
    if _BIRTH_CONTEXT.search(bridge):
        return False
    return not re.search(
        r"(?i)\b(?:паспорт\w*|выдан\w*|выдач\w*|конференц\w*|встреч\w*|"
        r"срок\w*|состоит\w*|телефон\w*|адрес\w*)\b", bridge,
    )


class BirthDateDetector(Detector):
    detector_id = "birth_date"
    detector_version = "1.3.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        # Find context markers.
        contexts = list(_BIRTH_CONTEXT.finditer(text))
        if not contexts:
            return out

        # Collect all candidate dates (numeric + ISO + words + written-day).
        dates = (
            list(_NUMERIC_DATE.finditer(text))
            + list(_ISO_DATE.finditer(text))
            + list(_WORDS_DATE.finditer(text))
            + list(_WORDS_DAY_DATE.finditer(text))
        )
        dates.sort(key=lambda m: m.start())
        date_starts = [match.start() for match in dates]

        def attached_date(context):
            # Form labels usually precede the value. Do not let an earlier
            # event date win merely because its character distance is shorter.
            index = bisect_left(date_starts, context.end())
            for candidate in range(index, len(dates)):
                match = dates[candidate]
                if match.start() - context.end() > 120:
                    break
                if _birth_date_bridge(text[context.end():match.start()]):
                    return match
            # Also support an explicit reversed field: «12.04.1990 — дата
            # рождения». Only separators may join the value to this label.
            for candidate in range(index - 1, -1, -1):
                match = dates[candidate]
                if context.start() - match.end() > 12:
                    break
                if match.end() <= context.start():
                    bridge = text[match.end():context.start()]
                    if re.fullmatch(r"[ \t,:(—–-]*", bridge):
                        return match
            return None

        for c in contexts:
            if c.group().lower() in ("родился", "родилась") and has_public_subject(text, c.start()):
                continue
            m = attached_date(c)
            if m is None:
                continue
            start, end = m.start(), m.end()
            # Exclude a trailing "года" service word from the sensitive span (R13).
            sensitive_end = end
            tail = text[start:end].lower()
            if tail.endswith(" года"):
                sensitive_end = end - len(" года")
            # Deduplicate: skip if this exact span was already emitted.
            if any(s.sensitive_spans and s.sensitive_spans[0].start == start
                   and s.sensitive_spans[0].end == sensitive_end for s in out):
                continue
            parsed = _parse_date_match(m)
            if parsed is None:
                continue
            day, month, year = parsed
            if not _is_plausible_date(day, month, year):
                continue
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="BIRTH_DATE",
                    evidence_spans=(Span(start, end),),
                    sensitive_spans=(Span(start, sensitive_end),),
                    signals=(Signal("birth_context", 1.0), Signal("date", 1.0)),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.9,
                    decision=Decision.MASK,
                    rule_id="birth-date-context",
                )
            )
        return out
