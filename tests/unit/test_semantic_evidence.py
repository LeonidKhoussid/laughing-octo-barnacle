"""Explanations must identify the actual policy without extra inference."""
from types import SimpleNamespace

import pytest

from app.core.models import Decision, Span
from app.detectors.semantic import SemanticDetector, _detection, _PRIVATE_HYPOTHESIS


@pytest.mark.parametrize("text, name, decision, rule, signal, calls", [
    ("Александр Сергеевич Пушкин", "Александр Сергеевич Пушкин", Decision.KEEP,
     "historical-public-reference", "historical_public_reference", 0),
    ("Университет имени Николая Фёдоровича Соколова", "Николая Фёдоровича Соколова", Decision.KEEP,
     "institution-dedication", "institution_dedication", 0),
    ("Драматург Бернард Шоу написал пьесу.", "Бернард Шоу", Decision.KEEP,
     "semantic-public-context", "local_nli_public_context", 1),
    ("Клиент Бернард Шоу", "Бернард Шоу", Decision.MASK,
     "semantic-private-record", "private_record_guard", 0),
    ("Бернард Шоу", "Бернард Шоу", Decision.MASK,
     "semantic-uncertain-protected", "context_uncertain_protected", 0),
])
def test_real_evidence_origin_and_no_explanation_inference(text, name, decision, rule, signal, calls):
    instance = SemanticDetector.__new__(SemanticDetector)
    invocations = []
    def nli(pairs):
        invocations.append(pairs)
        return [(.01, .98, .01) if hypothesis == _PRIVATE_HYPOTHESIS else (.95, .03, .02)
                for _, hypothesis in pairs]
    instance.runtime = SimpleNamespace(ner=lambda text: [], nli=nli)
    start = text.index(name)
    span = Span(start, start + len(name))
    result, = instance.refine(text, [_detection(span, .9)])
    assert result.decision == decision
    assert result.rule_id == rule
    assert signal in {s.name for s in result.signals}
    assert len(invocations) == calls
    if signal != "local_nli_public_context":
        assert "local_nli_public_context" not in {s.name for s in result.signals}
    assert result.sensitive_spans == (() if decision == Decision.KEEP else (span,))
    assert result.evidence_spans == (span,)
