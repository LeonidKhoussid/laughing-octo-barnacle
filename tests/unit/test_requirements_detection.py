"""Focused synthetic checks for every required detector category."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.models import Decision, Span
from app.detectors.address import AddressDetector
from app.detectors.card_security import CardholderNameDetector, CvvDetector, PinDetector
from app.detectors.dates import BirthDateDetector
from app.detectors.documents import DriverLicenseDetector, PassportIssueDateDetector, PassportIssuerDetector
from app.detectors.identity import BirthPlaceDetector, CitizenshipDetector
from app.detectors.person import FullNameDetector, _plausible
from app.detectors.semantic import SemanticDetector, _detection
from app.detectors.structured import CardDetector, DepartmentCodeDetector, EmailDetector, InnDetector, PassportDetector, PhoneDetector


FIXTURE = Path(__file__).parents[1] / "fixtures" / "requirements_detection_cases.json"
DETECTORS = {
    "EMAIL": EmailDetector, "PHONE": PhoneDetector, "CARD": CardDetector,
    "PASSPORT": PassportDetector, "FULL_NAME": FullNameDetector,
    "BIRTH_DATE": BirthDateDetector, "INN": InnDetector,
    "DEPARTMENT_CODE": DepartmentCodeDetector, "ADDRESS": AddressDetector,
    "BIRTH_PLACE": BirthPlaceDetector, "CITIZENSHIP": CitizenshipDetector,
    "PASSPORT_ISSUER": PassportIssuerDetector,
    "PASSPORT_ISSUE_DATE": PassportIssueDateDetector,
    "DRIVER_LICENSE": DriverLicenseDetector, "CVV": CvvDetector, "PIN": PinDetector,
    "CARDHOLDER_NAME": CardholderNameDetector,
}


def _values(detector, text):
    return [text[span.start:span.end] for detection in detector.detect(text)
            for span in detection.sensitive_spans]


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"], ids=lambda case: case["id"])
def test_required_detector_matrix(case):
    values = _values(DETECTORS[case["detector"]](), case["text"])
    assert values == case["sensitive"]


def test_public_author_and_private_homonym_have_per_subject_decisions():
    text = (
        "Открытый каталог: автор Нелли Антоновна Леснова представила картину. "
        "В закрытой заявке клиент Нелли Антоновна Леснова указала телефон."
    )
    name = "Нелли Антоновна Леснова"
    starts = [offset for offset in range(len(text)) if text.startswith(name, offset)]
    detector = SemanticDetector.__new__(SemanticDetector)
    detector.runtime = SimpleNamespace(
        ner=lambda _: [],
        nli=lambda pairs: [(.01, .98, .01) if "частные сведения" in hypothesis
                           else (.98, .01, .01)
                           for _, hypothesis in pairs],
    )
    result = detector.refine(text, [_detection(Span(start, start + len(name)), .9) for start in starts])
    by_start = {d.evidence_spans[0].start: d for d in result}
    assert by_start[starts[0]].decision == Decision.KEEP
    assert by_start[starts[1]].decision == Decision.MASK


def test_private_conversation_context_overrides_cultural_topic():
    text = "Мы с Иваном Петровым вчера обсуждали литературу."
    assert _values(FullNameDetector(), text) == ["Иваном Петровым"]


def test_precomputed_roles_are_not_mutated_by_unknown_name_handling():
    roles = [frozenset(), frozenset({"patronymic"}), frozenset({"last"})]
    assert _plausible(["Александер", "Сергеевич", "Пушкин"], True, True, roles)
    assert roles[0] == frozenset()


def test_cardholder_does_not_consume_next_record():
    assert _values(CardholderNameDetector(), "Cardholder: John Smith\nКонтрольная метка: x") == ["John Smith"]
    assert _values(CardholderNameDetector(), "Cardholder: JOHN\u00a0SMITH") == ["JOHN\u00a0SMITH"]
