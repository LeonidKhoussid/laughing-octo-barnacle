"""Avoid redundant inference without changing the context decision policy."""
from itertools import product
from types import SimpleNamespace

import pytest

from app.detectors.semantic import SemanticDetector
from app.nlp.model_runtime import ModelUnavailable


def detector(outputs):
    instance = SemanticDetector.__new__(SemanticDetector)
    calls = []
    def nli(pairs):
        calls.append(pairs)
        return [outputs[pair] for pair in pairs]
    instance.runtime = SimpleNamespace(nli=nli)
    return instance, calls


@pytest.mark.parametrize("outcomes", product([(.95, .03, .02), (.60, .35, .05), (.05, .9, .05)], repeat=3))
def test_decision_matches_exhaustive_policy(outcomes):
    passage = "Описание произведения"
    hypotheses = ["литература", "искусство", "музыка"]
    instance, calls = detector(dict(zip([(passage, h) for h in hypotheses], outcomes)))
    decision, score = instance._context_decisions({"person": (passage, hypotheses)})["person"]
    qualifying = [i for i, (e, n, c) in enumerate(outcomes) if e >= .8 and e - max(n, c) >= .5]
    assert (decision == "public-context") == bool(qualifying)
    assert len(calls) == (qualifying[0] + 1 if qualifying else len(hypotheses))
    assert score == (outcomes[qualifying[0]][0] if qualifying else 0.0)


def test_shared_passage_is_deduplicated_but_not_cached_across_requests():
    job = ("Описание произведения", ["литература", "искусство"])
    instance, calls = detector({(job[0], "литература"): (.95, .03, .02)})
    first = instance._context_decisions({"a": job, "b": job})
    assert first['a'] == first['b']
    assert calls == [[(job[0], "литература")]]
    instance._context_decisions({"a": job})
    assert len(calls) == 2


def test_different_job_lengths_and_explicit_reference_threshold():
    instance, calls = detector({("учебник", "a"): (.7, .25, .05),
                               ("обычный текст", "a"): (.7, .25, .05),
                               ("обычный текст", "b"): (.9, .05, .05)})
    result = instance._context_decisions({"short": ("учебник", ["a"]),
                                          "long": ("обычный текст", ["a", "b"])})
    assert result["short"] == ("public-context", .7)
    assert result["long"] == ("public-context", .9)
    assert calls[-1] == [("обычный текст", "b")]


def test_required_inference_error_still_fails_closed():
    instance, _ = detector({})
    def fail(pairs):
        raise ModelUnavailable()
    instance.runtime.nli = fail
    with pytest.raises(ModelUnavailable):
        instance._context_decisions({"a": ("passage", ["hypothesis"])})
