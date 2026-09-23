"""Evaluate the PII engine against independent labeled fixtures.

Computes LOCAL DIAGNOSTIC metrics (NOT the organizers' official formula):
  - Entity precision/recall/F1 at exact span match (per category + macro).
  - Boundary accuracy: fraction of predicted spans exactly matching a gold span.
  - Concealment completeness (character-level recall): |G ∩ P| / |G|.
  - Span character precision: |G ∩ P| / |P|.
  - Non-PII overmask rate: |P \\ G| / |all_positions \\ G|.
  - Round-trip accuracy: unmask(mask(x)) == x.
  - Fully/partially missed sensitive entities.
  - Fraction of requests with at least one missed labeled sensitive fragment.

These formulas are our diagnostic tool (master prompt section 16.2), not the
documented hackathon formula. Empty denominators are handled explicitly.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from app.core.engine import Engine
from app.detectors.address import AddressDetector
from app.detectors.card_security import CardholderNameDetector, CvvDetector, PinDetector
from app.detectors.dates import BirthDateDetector
from app.detectors.documents import (
    DriverLicenseDetector,
    PassportIssueDateDetector,
    PassportIssuerDetector,
)
from app.detectors.identity import BirthPlaceDetector, CitizenshipDetector
from app.detectors.person import FullNameDetector
from app.detectors.structured import (
    CardDetector,
    DepartmentCodeDetector,
    EmailDetector,
    InnDetector,
    PassportDetector,
    PhoneDetector,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "artifacts" / "fixtures.json"
REPORT = ROOT / "artifacts" / "evaluation_report.json"

DETECTORS = [
    EmailDetector(), PhoneDetector(), CardDetector(), PassportDetector(),
    FullNameDetector(), BirthDateDetector(), InnDetector(), DepartmentCodeDetector(),
    AddressDetector(), BirthPlaceDetector(), CitizenshipDetector(),
    PassportIssuerDetector(), PassportIssueDateDetector(), DriverLicenseDetector(),
    CvvDetector(), PinDetector(), CardholderNameDetector(),
]

ENGINE = Engine(DETECTORS)

ALL_CATEGORIES = [
    "FULL_NAME", "BIRTH_DATE", "BIRTH_PLACE", "PASSPORT", "CITIZENSHIP",
    "PASSPORT_ISSUER", "DEPARTMENT_CODE", "PASSPORT_ISSUE_DATE", "DRIVER_LICENSE",
    "ADDRESS", "EMAIL", "PHONE", "INN", "CARD", "CVV", "PIN", "CARDHOLDER_NAME",
]


def _positions(spans: list[dict]) -> set[int]:
    pos = set()
    for s in spans:
        pos.update(range(s["start"], s["end"]))
    return pos


def _exact_match(gold: dict, preds: list[dict]) -> bool:
    """Exact match requires the SAME category AND exact boundaries (one-to-one)."""
    for p in preds:
        if (
            p["start"] == gold["start"]
            and p["end"] == gold["end"]
            and p["category"] == gold["category"]
        ):
            return True
    return False


def _overlaps(gold: dict, preds: list[dict]) -> bool:
    gs, ge = gold["start"], gold["end"]
    for p in preds:
        ps, pe = p["start"], p["end"]
        if gs < pe and ps < ge:
            return True
    return False


def evaluate() -> dict:
    if not FIXTURES.exists():
        # Reproducible prerequisite: generate fixtures on-the-fly if the file is
        # missing (e.g. after a clean checkout where artifacts/ is excluded).
        from generate_fixtures import build, write

        write(build(), FIXTURES)
    with open(FIXTURES, "r", encoding="utf-8") as f:
        data = json.load(f)
    examples = data["examples"]

    # Per-category entity counters.
    cat_tp = defaultdict(int)
    cat_gold = defaultdict(int)
    cat_pred = defaultdict(int)
    # Character-level per category (accumulated per-document, then averaged).
    cat_g = defaultdict(set)
    cat_p = defaultdict(set)
    # Per-document character recall/precision for correct macro averaging.
    doc_conceal_recalls: list[float] = []
    doc_span_precisions: list[float] = []
    # Round-trip.
    roundtrip_ok = 0
    roundtrip_total = 0
    # Missed entities.
    fully_missed = 0
    partially_missed = 0
    requests_with_miss = 0
    total_requests = 0
    # Boundary accuracy.
    boundary_exact = 0
    boundary_total = 0
    # Overmasking (per-document, then averaged).
    doc_overmask_rates: list[float] = []

    per_example = []

    for ex in examples:
        text = ex["text"]
        gold = ex["gold_spans"]
        total_requests += 1

        result = ENGINE.mask(text, "eval-ns", "eval-ctx")
        preds = [
            {"start": rs.span.start, "end": rs.span.end, "category": rs.category}
            for rs in result.resolved_spans
        ]

        # Round-trip.
        roundtrip_total += 1
        if ENGINE.unmask(result.masked_text, result.token_mapping) == text:
            roundtrip_ok += 1

        # Per-category entity metrics.
        for g in gold:
            cat = g["category"]
            cat_gold[cat] += 1
            cat_g[cat].update(range(g["start"], g["end"]))
            if _exact_match(g, preds):
                cat_tp[cat] += 1
            elif _overlaps(g, preds):
                partially_missed += 1
            else:
                fully_missed += 1

        for p in preds:
            cat = p["category"]
            cat_pred[cat] += 1
            cat_p[cat].update(range(p["start"], p["end"]))
            boundary_total += 1
            if any(
                g["start"] == p["start"] and g["end"] == p["end"] and g["category"] == p["category"]
                for g in gold
            ):
                boundary_exact += 1

        # Request-level miss.
        if any(not _exact_match(g, preds) for g in gold):
            requests_with_miss += 1

        # Per-document character-level metrics (NOT merged across documents).
        G = _positions(gold)
        P = _positions(preds)
        if G:
            doc_conceal_recalls.append(len(G & P) / len(G))
        if P:
            doc_span_precisions.append(len(G & P) / len(P))
        non_pii = set(range(len(text))) - G
        if non_pii:
            doc_overmask_rates.append(len(P - G) / len(non_pii))

        per_example.append(
            {
                "category": ex["category"],
                "scenario": ex["scenario"],
                "text": text,
                "gold_spans": gold,
                "pred_spans": preds,
                "roundtrip_ok": ENGINE.unmask(result.masked_text, result.token_mapping) == text,
            }
        )

    # Per-category metrics.
    per_category = {}
    for cat in ALL_CATEGORIES:
        tp = cat_tp[cat]
        g = cat_gold[cat]
        p = cat_pred[cat]
        prec = tp / p if p else None
        rec = tp / g if g else None
        f1 = (2 * prec * rec / (prec + rec)) if (prec is not None and rec is not None and (prec + rec) > 0) else None
        G = cat_g[cat]
        P = cat_p[cat]
        conceal = len(G & P) / len(G) if G else None
        span_prec = len(G & P) / len(P) if P else None
        per_category[cat] = {
            "gold_entities": g,
            "pred_entities": p,
            "exact_tp": tp,
            "precision": prec,
            "recall": rec,
            "f1": f1,
            "concealment_recall": conceal,
            "span_character_precision": span_prec,
        }

    # Macro aggregate over categories that have gold entities.
    macro_prec = [per_category[c]["precision"] for c in ALL_CATEGORIES if per_category[c]["precision"] is not None]
    macro_rec = [per_category[c]["recall"] for c in ALL_CATEGORIES if per_category[c]["recall"] is not None]
    macro_f1 = [per_category[c]["f1"] for c in ALL_CATEGORIES if per_category[c]["f1"] is not None]
    macro = {
        "precision": sum(macro_prec) / len(macro_prec) if macro_prec else None,
        "recall": sum(macro_rec) / len(macro_rec) if macro_rec else None,
        "f1": sum(macro_f1) / len(macro_f1) if macro_f1 else None,
    }

    # Global character-level (per-document averaged, NOT merged across docs).
    conceal_recall = sum(doc_conceal_recalls) / len(doc_conceal_recalls) if doc_conceal_recalls else None
    span_char_prec = sum(doc_span_precisions) / len(doc_span_precisions) if doc_span_precisions else None
    overmask = sum(doc_overmask_rates) / len(doc_overmask_rates) if doc_overmask_rates else None

    report = {
        "schema_version": 1,
        "note": "LOCAL DIAGNOSTIC metrics, not the organizers' official formula (master prompt 16.2).",
        "fixtures": str(FIXTURES),
        "total_examples": total_requests,
        "per_category": per_category,
        "macro_aggregate": macro,
        "global_character_level": {
            "concealment_recall": conceal_recall,
            "span_character_precision": span_char_prec,
            "non_pii_overmask_rate": overmask,
        },
        "round_trip": {
            "ok": roundtrip_ok,
            "total": roundtrip_total,
            "accuracy": roundtrip_ok / roundtrip_total if roundtrip_total else None,
        },
        "missed_entities": {
            "fully_missed": fully_missed,
            "partially_missed": partially_missed,
        },
        "requests_with_missed_fragment": {
            "count": requests_with_miss,
            "fraction": requests_with_miss / total_requests if total_requests else None,
        },
        "boundary_accuracy": {
            "exact": boundary_exact,
            "total": boundary_total,
            "accuracy": boundary_exact / boundary_total if boundary_total else None,
        },
        "per_example": per_example,
    }

    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def _fmt(v) -> str:
    if v is None:
        return "  n/a "
    return f"{v:6.3f}"


def print_summary(report: dict) -> None:
    print("=" * 78)
    print("LOCAL DIAGNOSTIC EVALUATION (not the official formula)")
    print("=" * 78)
    print(f"Total examples: {report['total_examples']}")
    print()
    print(f"{'Category':<22}{'P':>6}{'R':>6}{'F1':>6}{'Conceal':>8}{'SpanP':>7}")
    print("-" * 78)
    for cat in ALL_CATEGORIES:
        m = report["per_category"][cat]
        print(
            f"{cat:<22}{_fmt(m['precision']):>6}{_fmt(m['recall']):>6}"
            f"{_fmt(m['f1']):>6}{_fmt(m['concealment_recall']):>8}"
            f"{_fmt(m['span_character_precision']):>7}"
        )
    macro = report["macro_aggregate"]
    print("-" * 78)
    print(
        f"{'MACRO':<22}{_fmt(macro['precision']):>6}{_fmt(macro['recall']):>6}"
        f"{_fmt(macro['f1']):>6}"
    )
    print()
    g = report["global_character_level"]
    print("Global character-level (local diagnostic):")
    print(f"  concealment_recall        = {_fmt(g['concealment_recall'])}")
    print(f"  span_character_precision  = {_fmt(g['span_character_precision'])}")
    print(f"  non_pii_overmask_rate     = {_fmt(g['non_pii_overmask_rate'])}")
    print()
    rt = report["round_trip"]
    print(f"Round-trip accuracy: {rt['ok']}/{rt['total']} = {_fmt(rt['accuracy'])}")
    me = report["missed_entities"]
    print(f"Fully missed entities: {me['fully_missed']}, partially missed: {me['partially_missed']}")
    rw = report["requests_with_missed_fragment"]
    print(f"Requests with >=1 missed fragment: {rw['count']}/{report['total_examples']} = {_fmt(rw['fraction'])}")
    ba = report["boundary_accuracy"]
    print(f"Boundary accuracy (exact span match): {ba['exact']}/{ba['total']} = {_fmt(ba['accuracy'])}")
    print()
    print(f"Report saved to {REPORT}")


def main() -> int:
    report = evaluate()
    print_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
