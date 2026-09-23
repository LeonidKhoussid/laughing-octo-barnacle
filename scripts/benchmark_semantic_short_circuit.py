"""Compare exhaustive NLI policy with the current short-circuit implementation.

Both variants execute the same detectors and active FP32 weights. Only a
process-local method is substituted, and it is restored after the comparison.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys
import time
from types import MethodType

os.environ["ORT_DISABLE_TELEMETRY"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.engine import Engine
from app.detectors.registry import DetectorRegistry
from app.detectors.semantic import _EDUCATION, _PRIVATE
from app.nlp.model_runtime import file_sha256
from scripts.evaluate_semantic import character_outcome, key, summary, timing


def exhaustive_decisions(self, jobs):
    """Former policy: score every unique pair, then OR qualifying hypotheses."""
    pairs = list(dict.fromkeys((passage, hypothesis)
                              for passage, hypotheses in jobs.values()
                              for hypothesis in hypotheses))
    scores = dict(zip(pairs, self.runtime.nli(pairs))) if pairs else {}
    decisions = {}
    for item, (passage, hypotheses) in jobs.items():
        explicit_reference = bool(_EDUCATION.search(passage)) and not _PRIVATE.search(passage)
        threshold, margin = (.55, .10) if explicit_reference else (.80, .50)
        supported = [e for e, n, c in (scores[passage, h] for h in hypotheses)
                     if e >= threshold and e - max(n, c) >= margin]
        decisions[item] = ("public-context", max(supported)) if supported else ("uncertain-protected", 0.0)
    return decisions


class CountedRuntime:
    def __init__(self, runtime):
        self.runtime = runtime
        self.reset()

    def reset(self):
        self.pairs = self.ner_calls = self.nli_calls = 0
        self.nli_ms = 0.0

    def ner(self, text):
        self.ner_calls += 1
        return self.runtime.ner(text)

    def nli(self, pairs):
        self.nli_calls += 1
        self.pairs += len(pairs)
        started = time.perf_counter()
        scores = self.runtime.nli(pairs)
        self.nli_ms += (time.perf_counter() - started) * 1000
        return scores


def run(iterations):
    tracked = sorted({*ROOT.glob("app/detectors/*.py"),
                      ROOT / "app/nlp/model_runtime.py", ROOT / "app/core/engine.py",
                      ROOT / "configs/detectors.yaml", ROOT / "configs/semantic-models.json"})
    source_hashes = {str(path.relative_to(ROOT)): file_sha256(path) for path in tracked}
    fixture_paths = [ROOT / "tests/fixtures/semantic_context_holdout.json",
                     ROOT / "tests/fixtures/semantic_final_holdout.json"]
    cases = [dict(case, fixture=path.name) for path in fixture_paths
             for case in json.loads(path.read_text())["cases"]]
    registry = DetectorRegistry.from_config(str(ROOT / "configs/detectors.yaml"))
    semantic = next(item for item in registry.detectors if item.detector_id == "semantic_context")
    original_runtime, original_method = semantic.runtime, semantic._context_decisions
    counted = CountedRuntime(original_runtime)
    semantic.runtime = counted
    methods = {"exhaustive": MethodType(exhaustive_decisions, semantic),
               "short_circuit": original_method}
    engine = Engine(registry.detectors)
    reports = {name: [] for name in methods}
    try:
        for name, method in methods.items():
            semantic._context_decisions = method
            for index in range(2):
                engine.mask("Альберт Эйнштейн разработал теорию относительности. Клиент Иван Петров сообщил телефон +7 900 111-22-33.",
                            "short-circuit-warmup", f"{name}-{index}")
        for case_index, case in enumerate(cases):
            order = ("exhaustive", "short_circuit") if case_index % 2 == 0 else ("short_circuit", "exhaustive")
            for name in order:
                semantic._context_decisions = methods[name]
                predictions, mask_ms, unmask_ms, nli_ms = [], [], [], []
                pair_counts, ner_calls, nli_calls = [], [], []
                roundtrip_ok = True
                for iteration in range(iterations):
                    counted.reset()
                    started = time.perf_counter()
                    result = engine.mask(case["text"], "short-circuit-diagnostic", f"{name}-{case_index}-{iteration}")
                    mask_ms.append((time.perf_counter() - started) * 1000)
                    started = time.perf_counter()
                    restored = engine.unmask(result.masked_text, result.token_mapping)
                    unmask_ms.append((time.perf_counter() - started) * 1000)
                    roundtrip_ok &= restored == case["text"]
                    predictions.append([{"category": item.category, "start": item.span.start,
                                         "end": item.span.end, "text": case["text"][item.span.start:item.span.end]}
                                        for item in result.resolved_spans])
                    nli_ms.append(counted.nli_ms)
                    pair_counts.append(counted.pairs)
                    ner_calls.append(counted.ner_calls)
                    nli_calls.append(counted.nli_calls)
                predicted = predictions[0]
                gold_keys, predicted_keys = set(map(key, case["gold"])), set(map(key, predicted))
                reports[name].append({**case, "predicted": predicted,
                                      "missing": [s for s in case["gold"] if key(s) not in predicted_keys],
                                      "unexpected": [s for s in predicted if key(s) not in gold_keys],
                                      "exact": gold_keys == predicted_keys,
                                      "stable_across_iterations": all(p == predicted for p in predictions),
                                      "roundtrip_ok": roundtrip_ok, "mask_ms": mask_ms, "unmask_ms": unmask_ms,
                                      "nli_ms": nli_ms, "nli_pairs": pair_counts,
                                      "ner_calls": ner_calls, "nli_calls": nli_calls,
                                      **character_outcome(case["text"], case["gold"], predicted)})
    finally:
        semantic.runtime, semantic._context_decisions = original_runtime, original_method
    comparisons = [{"id": before["id"], "fixture": before["fixture"], "tier": before["tier"],
                    "spans_identical": before["predicted"] == after["predicted"],
                    "exhaustive_pairs": before["nli_pairs"], "short_circuit_pairs": after["nli_pairs"],
                    "both_roundtrips_ok": before["roundtrip_ok"] and after["roundtrip_ok"]}
                   for before, after in zip(reports["exhaustive"], reports["short_circuit"])]
    return {
        "note": "LOCAL SYNTHETIC REGRESSION COMPARISON; not accuracy or throughput proof. Only the NLI scheduling policy differs.",
        "python": platform.python_version(), "platform": platform.platform(), "iterations": iterations,
        "fixture_sha256": {str(path.relative_to(ROOT)): file_sha256(path) for path in fixture_paths},
        "source_sha256": source_hashes,
        "source_unchanged_during_run": all(file_sha256(ROOT / name) == digest for name, digest in source_hashes.items()),
        "model_manifest": original_runtime.manifest,
        "comparisons": comparisons, "results": reports,
        "summary": {name: {
            "required": summary([r for r in rows if r["tier"] == "required"]),
            "diagnostic": summary([r for r in rows if r["tier"] == "diagnostic"]),
            "all_mask_timing": timing([value for row in rows for value in row["mask_ms"]]),
            "nli_only_timing": timing([value for row in rows for value in row["nli_ms"] if value > 0]),
            "nli_pairs": sum(sum(row["nli_pairs"]) for row in rows),
            "ner_calls": sum(sum(row["ner_calls"]) for row in rows),
            "nli_calls": sum(sum(row["nli_calls"]) for row in rows),
        } for name, rows in reports.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/semantic-short-circuit-comparison.json")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    report = run(args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"identical_cases": sum(c["spans_identical"] for c in report["comparisons"]),
                      "cases": len(report["comparisons"]), "summary": report["summary"],
                      "source_unchanged_during_run": report["source_unchanged_during_run"]}, ensure_ascii=False))
    return 0 if (report["source_unchanged_during_run"]
                 and all(c["spans_identical"] and c["both_roundtrips_ok"] for c in report["comparisons"])) else 1


if __name__ == "__main__":
    raise SystemExit(main())
