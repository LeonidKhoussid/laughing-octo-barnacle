"""Evaluate the independent challenge set (Layer B) per category.

Reports concealment, false positives, categorization, and restoration separately.
This is a LOCAL DIAGNOSTIC metric, not the organizers' official formula.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.engine import Engine  # noqa: E402
from app.detectors.registry import DetectorRegistry  # noqa: E402

CHALLENGE = ROOT / "artifacts" / "challenge_set.json"
REPORT = ROOT / "artifacts" / "challenge_report.json"

ENGINE = Engine(DetectorRegistry.from_config(str(ROOT / "configs" / "detectors.yaml")).detectors)


def _positions(spans: list[dict]) -> set[int]:
    pos = set()
    for s in spans:
        pos.update(range(s["start"], s["end"]))
    return pos


def _exact_match(gold: dict, preds: list[dict]) -> bool:
    for p in preds:
        if p["start"] == gold["start"] and p["end"] == gold["end"] and p["category"] == gold["category"]:
            return True
    return False


def evaluate() -> dict:
    if not CHALLENGE.exists():
        # Reproducible prerequisite: generate the challenge set on-the-fly if
        # the file is missing (e.g. after a clean checkout where artifacts/ is
        # excluded).
        from generate_challenge import build, write

        write(build(), CHALLENGE)
    with open(CHALLENGE, "r", encoding="utf-8") as f:
        data = json.load(f)
    examples = data["examples"]

    cat_tp = defaultdict(int)
    cat_gold = defaultdict(int)
    cat_pred = defaultdict(int)
    doc_conceal_recalls: list[float] = []
    doc_overmask_rates: list[float] = []
    roundtrip_ok = 0
    roundtrip_total = 0
    fully_missed = 0
    partially_missed = 0
    false_positives = 0
    per_example = []

    for ex in examples:
        text = ex["text"]
        gold = ex["gold_spans"]
        result = ENGINE.mask(text, "challenge-ns", "challenge-ctx")
        preds = [
            {"start": rs.span.start, "end": rs.span.end, "category": rs.category}
            for rs in result.resolved_spans
        ]
        roundtrip_total += 1
        if ENGINE.unmask(result.masked_text, result.token_mapping) == text:
            roundtrip_ok += 1

        for g in gold:
            cat = g["category"]
            cat_gold[cat] += 1
            if _exact_match(g, preds):
                cat_tp[cat] += 1
            elif any(g["start"] < p["end"] and p["start"] < g["end"] for p in preds):
                partially_missed += 1
            else:
                fully_missed += 1

        for p in preds:
            cat = p["category"]
            cat_pred[cat] += 1
            if not any(g["start"] == p["start"] and g["end"] == p["end"] and g["category"] == p["category"] for g in gold):
                false_positives += 1

        G = _positions(gold)
        P = _positions(preds)
        if G:
            doc_conceal_recalls.append(len(G & P) / len(G))
        non_pii = set(range(len(text))) - G
        if non_pii:
            doc_overmask_rates.append(len(P - G) / len(non_pii))

        per_example.append({
            "category": ex["category"],
            "scenario": ex["scenario"],
            "text": text,
            "gold_spans": gold,
            "pred_spans": preds,
            "roundtrip_ok": ENGINE.unmask(result.masked_text, result.token_mapping) == text,
        })

    # Per-category precision/recall/F1.
    per_cat = {}
    for cat in sorted(set(cat_gold) | set(cat_pred)):
        tp = cat_tp[cat]
        g = cat_gold[cat]
        p = cat_pred[cat]
        prec = tp / p if p else None
        rec = tp / g if g else None
        f1 = (2 * prec * rec / (prec + rec)) if (prec is not None and rec and prec + rec > 0) else None
        per_cat[cat] = {"gold": g, "pred": p, "tp": tp, "precision": prec, "recall": rec, "f1": f1}

    report = {
        "note": "LOCAL DIAGNOSTIC — not the organizers' official formula.",
        "total_examples": len(examples),
        "roundtrip": {"ok": roundtrip_ok, "total": roundtrip_total},
        "fully_missed": fully_missed,
        "partially_missed": partially_missed,
        "false_positives": false_positives,
        "concealment_recall": sum(doc_conceal_recalls) / len(doc_conceal_recalls) if doc_conceal_recalls else None,
        "overmask_rate": sum(doc_overmask_rates) / len(doc_overmask_rates) if doc_overmask_rates else None,
        "per_category": per_cat,
        "examples": per_example,
    }
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main() -> int:
    report = evaluate()
    print(f"Total examples: {report['total_examples']}")
    print(f"Round-trip: {report['roundtrip']['ok']}/{report['roundtrip']['total']}")
    print(f"Fully missed: {report['fully_missed']}, partially missed: {report['partially_missed']}, false positives: {report['false_positives']}")
    print(f"Concealment recall: {report['concealment_recall']:.3f}, overmask rate: {report['overmask_rate']:.3f}")
    print("\nPer-category:")
    for cat, m in report["per_category"].items():
        print(f"  {cat}: gold={m['gold']} pred={m['pred']} tp={m['tp']} "
              f"P={m['precision']} R={m['recall']} F1={m['f1']}")
    print(f"\nReport saved to {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())