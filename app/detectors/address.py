"""ADDRESS detector (basic): city/street/house/apartment components. Minimal for G1."""
from __future__ import annotations

import re
import uuid

from app.core.models import Decision, Detection, Signal, Span
from app.detectors.base import Detector

# Address context markers.
_ADDRESS_CONTEXT = re.compile(
    r"(?i)(адрес|адреса|адресу|адресом|проживает|проживающ|зарегистрирован|"
    r"зарегистрирована|место\s+жительства|место\s+проживания|\bиз\s+дома\b)"
)

# Office/organization context that should NOT trigger personal-address masking.
# Broadened to cover "Отделение банка расположено по адресу" (C3).
_OFFICE_CONTEXT = re.compile(
    r"(?i)(адрес\s+отделения|адрес\s+офиса|адрес\s+банка|адрес\s+филиала|"
    r"адрес\s+организации|адрес\s+компании|адрес\s+представительства|"
    r"отделение\s+банка|офис\s+банка|филиал\s+банка|банк\s+расположен|"
    r"отделение\s+расположено|офис\s+расположен|банкомат\s+расположен)"
)

# Personal-address context: a component after one of these markers belongs to a
# client, even if an office marker appeared earlier in the text (master prompt
# 9.1: one "офис" must not make the whole document non-PII).
_PERSONAL_ADDRESS_CONTEXT = re.compile(
    r"(?i)(адрес\s+клиента|адрес\s+проживания|адрес\s+регистрации|"
    r"проживает|проживающ|зарегистрирован|зарегистрирована|"
    r"место\s+жительства|место\s+проживания|домашний\s+адрес|\bиз\s+дома\b)"
)

# City: "г. Москва" or "город Москва" or compact "г.Москва". Multiword cities
# like "Нижний Новгород" are captured.
_CITY = re.compile(
    r"(?i)(?:г\.|город)\s*([А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+)?)"
)
# Street or alley, including compact "ул.Тверская" and "пер.Тихий".
# Handles numeric street names like "8 Марта".
_STREET = re.compile(
    r"(?i)(?:ул\.|улица|пер\.|переулок)\s*([А-ЯЁ0-9][а-яё0-9\-]*(?:\s+[А-ЯЁ0-9][а-яё0-9\-]+)?)"
)
# House: "д. 15" or "дом 15" or "д. 15к2" or compact "д.15".
_HOUSE = re.compile(r"(?i)(?:д\.|дом)\s*(\d+[а-яё]?(?:[\/\-]\d+[а-яё]?)?)")
# Apartment: "кв. 42" or "квартира 42" or compact "кв.42".
_APARTMENT = re.compile(r"(?i)(?:кв\.|квартира)\s*(\d+)")
# Country: "Россия" after an address context (e.g. "Адрес клиента: Россия, ...").
# Also matches a known country name at the start of an address.
_COUNTRY = re.compile(r"(?i)(?:страна|страны|страну)[\s:]*([А-ЯЁ][а-яё\-]+)")
# Known country names that can appear at the start of an address.
_COUNTRY_NAMES = (
    "россия", "рф", "беларусь", "белоруссия", "казахстан", "украина",
    "германия", "франция", "италия", "испания", "сша", "китай", "индия",
    "турция", "польша", "чехия", "финляндия", "швеция", "норвегия",
    "великобритания", "англия", "япония", "корея", "израиль", "грузия",
    "армения", "азербайджан", "узбекистан", "таджикистан", "киргизия",
    "молдова", "латвия", "литва", "эстония",
)
_COUNTRY_AT_START = re.compile(
    rf"(?i)(?:^|[,;:]\s*)({'|'.join(_COUNTRY_NAMES)})(?=\s*[,;]|\s*$)"
)
# Postcode: 6 digits after an address context.
_POSTCODE = re.compile(r"(?<!\w)(\d{6})(?!\w)")


class AddressDetector(Detector):
    detector_id = "address"
    detector_version = "1.3.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        contexts = list(_ADDRESS_CONTEXT.finditer(text))
        offices = list(_OFFICE_CONTEXT.finditer(text))

        personal = list(_PERSONAL_ADDRESS_CONTEXT.finditer(text))

        def is_office(pos: int) -> bool:
            # An office marker before the component marks the address as
            # organizational, UNLESS a personal-address marker appears between
            # the office marker and the component (then it is a client address).
            for o in offices:
                if not (o.start() - 5 <= pos <= o.end() + 120):
                    continue
                # A personal marker between the office marker and the component
                # overrides the office classification.
                if any(p.start() > o.end() and p.end() <= pos for p in personal):
                    continue
                return True
            return False

        def near_context(pos: int) -> bool:
            for c in contexts:
                if c.start() - 30 <= pos <= c.end() + 120:
                    return True
            return False

        # Collect sensitive component spans.
        sensitive: list[Span] = []
        for m in _CITY.finditer(text):
            if near_context(m.start()) and not is_office(m.start()):
                sensitive.append(Span(m.start(1), m.end(1)))
        for m in _STREET.finditer(text):
            if near_context(m.start()) and not is_office(m.start()):
                sensitive.append(Span(m.start(1), m.end(1)))
        for m in _HOUSE.finditer(text):
            if near_context(m.start()) and not is_office(m.start()):
                sensitive.append(Span(m.start(1), m.end(1)))
        for m in _APARTMENT.finditer(text):
            if near_context(m.start()) and not is_office(m.start()):
                sensitive.append(Span(m.start(1), m.end(1)))
        for m in _COUNTRY.finditer(text):
            if near_context(m.start()) and not is_office(m.start()):
                sensitive.append(Span(m.start(1), m.end(1)))
        for m in _COUNTRY_AT_START.finditer(text):
            if near_context(m.start()) and not is_office(m.start()):
                sensitive.append(Span(m.start(1), m.end(1)))
        # Postcode: a 6-digit number near an address context (e.g. "Россия,
        # 350000, г. Краснодар"). Only when an address context is present.
        for m in _POSTCODE.finditer(text):
            if near_context(m.start()) and not is_office(m.start()):
                sensitive.append(Span(m.start(1), m.end(1)))

        if not sensitive:
            return []

        # Emit components in source order. This keeps evidence and sensitive
        # spans deterministic for one address containing every component.
        sensitive.sort(key=lambda span: (span.start, span.end))

        # Evidence is the whole address region (from first to last component).
        first = min(s.start for s in sensitive)
        last = max(s.end for s in sensitive)
        out.append(
            Detection(
                entity_id=str(uuid.uuid4()),
                category="ADDRESS",
                evidence_spans=(Span(first, last),),
                sensitive_spans=tuple(sensitive),
                signals=(Signal("address_context", 1.0),),
                detector_id=self.detector_id,
                detector_version=self.detector_version,
                score=0.8,
                decision=Decision.MASK,
                rule_id="address-components-context",
            )
        )
        return out
