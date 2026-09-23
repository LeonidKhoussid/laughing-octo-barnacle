"""CVV, PIN, CARDHOLDER_NAME detectors.

CVV/PIN require an explicit signature/context and a short number; a bare short
number (port, year, order code) is never classified as a secret. CARDHOLDER_NAME
supports both Cyrillic and Latin names and rejects trading organizations.
"""
from __future__ import annotations

import re
import uuid

from app.core.models import Decision, Detection, Signal, Span
from app.detectors.base import Detector
from app.detectors.person import _roles

_CVV_RE = re.compile(
    r"(?i)(CVV-код|CVC-код|CVV|CVC|код\s+безопасности|код\s+с\s+обратной\s+стороны)"
    r"[ \t:]*"
    r"(\d{3,4})"
)

_PIN_RE = re.compile(
    r"(?i)(ПИН-код\s+карты|PIN-код\s+карты|пин-код\s+карты|пин\s+код\s+карты|"
    r"PIN-код|ПИН-код|пин-код|пин\s+код|PIN|ПИН)"
    r"[ \t:]*"
    r"(\d{3,8})"
)

# Cardholder name: a run of 1-4 capitalized name tokens. Stop at non-name words
# (verbs, prepositions, conjunctions) so "Иван Иванов сообщил" captures only
# "Иван Иванов" (C3).
_CARDHOLDER_RE = re.compile(
    r"(?i)\b(держатель\s+карты|держатель|cardholder|имя\s+на\s+карте|имя\s+на\s+карточке)\b"
    r"[ \t:]*"
    r"([А-ЯЁA-Z][а-яёa-z\-]+(?:[^\S\r\n]+[А-ЯЁA-Z][а-яёa-z\-]+){0,3})"
)

# Words that terminate a cardholder name run (verbs, prepositions, etc.).
_CARDHOLDER_STOP = re.compile(
    r"(?i)^(сообщил|сообщила|сказал|сказала|заявил|заявила|указал|указала|"
    r"предоставил|предоставила|обратился|обратилась|позвонил|позвонила|"
    r"написал|написала|подтвердил|подтвердила|отказался|отказалась|"
    r"и|или|а|но|что|который|которая|по|на|в|с|из|за|о|об|при|для|"
    r"является|был|была|было|есть|стал|стала)"
)

_ORG_PREFIX = re.compile(r"(?i)^(ООО|АО|ЗАО|ПАО|ОАО|ИП)\b")
_NON_CARD_OBJECT = re.compile(
    r"(?i)^(?:договор\w*|полис\w*|акци[яийюе]\w*|облигаци\w*|"
    r"сертификат\w*|патент\w*|ключ\w*|лицензи\w*|доверенност\w*)\b"
)


class CvvDetector(Detector):
    detector_id = "cvv"
    detector_version = "1.0.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        for m in _CVV_RE.finditer(text):
            start, end = m.start(2), m.end(2)
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="CVV",
                    evidence_spans=(Span(m.start(0), m.end(0)),),
                    sensitive_spans=(Span(start, end),),
                    signals=(Signal("cvv_context", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.9,
                    decision=Decision.MASK,
                    rule_id="cvv-context",
                )
            )
        return out


class PinDetector(Detector):
    detector_id = "pin"
    detector_version = "1.0.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        for m in _PIN_RE.finditer(text):
            start, end = m.start(2), m.end(2)
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="PIN",
                    evidence_spans=(Span(m.start(0), m.end(0)),),
                    sensitive_spans=(Span(start, end),),
                    signals=(Signal("pin_context", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.9,
                    decision=Decision.MASK,
                    rule_id="pin-context",
                )
            )
        return out


class CardholderNameDetector(Detector):
    detector_id = "cardholder_name"
    detector_version = "1.2.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        for m in _CARDHOLDER_RE.finditer(text):
            name = m.group(2)
            if _ORG_PREFIX.search(name):
                continue
            if m.group(1).lower() == "держатель" and _NON_CARD_OBJECT.match(name):
                continue
            # Validate from the first word, stopping before prose rather than
            # trimming a few verbs only at the end of an overbroad match.
            # Match offsets preserve NBSP and repeated spaces in the original.
            words = list(re.finditer(r"\S+", name))
            accepted = []
            for word in words:
                value = word.group()
                if _CARDHOLDER_STOP.fullmatch(value):
                    break
                if not (value[0].isupper() or _roles(value)):
                    # Lowercase Latin card-print names have no Russian roles.
                    # Accept their conventional two-word form, not later prose.
                    if not (len(accepted) < 2 and value.isascii()):
                        break
                accepted.append(word)
            if not accepted:
                continue
            start = m.start(2)
            end = start + accepted[-1].end()
            out.append(
                Detection(
                    entity_id=str(uuid.uuid4()),
                    category="CARDHOLDER_NAME",
                    evidence_spans=(Span(m.start(0), m.end(0)),),
                    sensitive_spans=(Span(start, end),),
                    signals=(Signal("cardholder_context", 1.0),),
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    score=0.85,
                    decision=Decision.MASK,
                    rule_id="cardholder-context",
                )
            )
        return out
