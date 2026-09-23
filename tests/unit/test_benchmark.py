"""Tests for the independent fixture generator and evaluator.

These verify that the benchmark tooling itself is correct: gold spans are valid,
and the evaluator's metric math is right on a tiny hand-checked example.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from generate_fixtures import build  # noqa: E402
import evaluate as evaluate_module  # noqa: E402
from evaluate import evaluate, _exact_match  # noqa: E402
from generate_challenge import build as build_challenge  # noqa: E402
from evaluate_challenge import evaluate as evaluate_challenge  # noqa: E402


class TestFixtureGenerator:
    def test_all_17_categories_present(self):
        examples = build()
        cats = {ex["category"] for ex in examples}
        expected = {
            "FULL_NAME", "BIRTH_DATE", "BIRTH_PLACE", "PASSPORT", "CITIZENSHIP",
            "PASSPORT_ISSUER", "DEPARTMENT_CODE", "PASSPORT_ISSUE_DATE",
            "DRIVER_LICENSE", "ADDRESS", "EMAIL", "PHONE", "INN", "CARD",
            "CVV", "PIN", "CARDHOLDER_NAME",
        }
        assert cats == expected

    def test_gold_spans_valid(self):
        examples = build()
        for ex in examples:
            text = ex["text"]
            for g in ex["gold_spans"]:
                assert g["start"] < g["end"], f"start<end violated: {ex['text']!r}"
                assert g["start"] >= 0 and g["end"] <= len(text), (
                    f"span out of bounds: {g} in {ex['text']!r}"
                )
                # The span must slice to a non-empty substring.
                assert text[g["start"]:g["end"]] != ""

    def test_gold_spans_are_ground_truth_not_predictions(self):
        # The generator must not depend on detector output; it only uses templates.
        examples = build()
        assert all("pred" not in ex for ex in examples)


class TestEvaluatorMetrics:
    def test_evaluator_runs_and_reports_per_category(self):
        report = evaluate()
        per_cat = report["per_category"]
        # All 17 categories must be present (not just an aggregate).
        assert len(per_cat) == 17
        for cat in per_cat:
            assert "precision" in per_cat[cat]
            assert "recall" in per_cat[cat]
            assert "f1" in per_cat[cat]
            assert "concealment_recall" in per_cat[cat]

    def test_roundtrip_accuracy_is_one(self):
        report = evaluate()
        assert report["round_trip"]["accuracy"] == 1.0

    def test_metrics_are_labeled_local_diagnostic(self):
        report = evaluate()
        assert "not the organizers' official formula" in report["note"]

    def test_exact_match_requires_category(self):
        """A prediction with the right span but wrong category is NOT a match."""
        gold = {"start": 7, "end": 18, "category": "FULL_NAME"}
        wrong_cat = [{"start": 7, "end": 18, "category": "EMAIL"}]
        right_cat = [{"start": 7, "end": 18, "category": "FULL_NAME"}]
        assert not _exact_match(gold, wrong_cat)
        assert _exact_match(gold, right_cat)

    def test_character_recall_not_merged_across_documents(self):
        """Two documents with the same span, one missed, must report 0.5
        character recall, not 1.0 (the old global-merge bug)."""
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        examples = [
            {
                "text": "Клиент Иванов Иван",
                "gold_spans": [{"start": 7, "end": 18, "category": "FULL_NAME"}],
                "category": "FULL_NAME",
                "scenario": "reviewer-controlled-test",
            },
            {
                "text": "Клиент Иванов Иван",
                "gold_spans": [{"start": 7, "end": 18, "category": "FULL_NAME"}],
                "category": "FULL_NAME",
                "scenario": "reviewer-controlled-test",
            },
        ]

        # Mock the engine so the SECOND document is completely missed (no spans).
        # This isolates the evaluator's per-document math from the detector.
        class _MissSecond:
            def __init__(self, real):
                self._real = real
                self._n = 0

            def mask(self, *a, **kw):
                self._n += 1
                if self._n == 2:
                    from app.core.engine import MaskResult
                    return MaskResult(masked_text=a[0], token_mapping={})
                return self._real.mask(*a, **kw)

            def unmask(self, *a, **kw):
                return self._real.unmask(*a, **kw)

        with tempfile.TemporaryDirectory() as tmp:
            fixtures = Path(tmp) / "fixtures.json"
            fixtures.write_text(json.dumps({"examples": examples}), encoding="utf-8")
            with patch("evaluate.FIXTURES", fixtures), patch(
                "evaluate.ENGINE", _MissSecond(evaluate_module.ENGINE)
            ):
                report = evaluate()
        # Character recall must be 0.5 (one of two documents fully missed).
        assert report["global_character_level"]["concealment_recall"] == 0.5


class TestChallengeSet:
    def test_challenge_set_covers_all_categories(self):
        """The independent challenge set must cover all 17 categories."""
        examples = build_challenge()
        cats = {ex["category"] for ex in examples}
        required = {
            "FULL_NAME", "BIRTH_DATE", "BIRTH_PLACE", "PASSPORT", "CITIZENSHIP",
            "PASSPORT_ISSUER", "DEPARTMENT_CODE", "PASSPORT_ISSUE_DATE",
            "DRIVER_LICENSE", "ADDRESS", "EMAIL", "PHONE", "INN", "CARD",
            "CVV", "PIN", "CARDHOLDER_NAME",
        }
        assert required <= cats, f"missing categories: {required - cats}"

    def test_challenge_set_has_negatives_and_variations(self):
        examples = build_challenge()
        scenarios = {ex["scenario"] for ex in examples}
        assert any("negative" in s for s in scenarios)
        assert any("iso" in s or "us" in s or "compound" in s for s in scenarios)

    def test_challenge_evaluation_per_category(self):
        """The challenge evaluation reports per-category results, not just an
        aggregate."""
        report = evaluate_challenge()
        assert "per_category" in report
        assert len(report["per_category"]) >= 17
        # Round-trip must be exact for all examples.
        assert report["roundtrip"]["ok"] == report["roundtrip"]["total"]
