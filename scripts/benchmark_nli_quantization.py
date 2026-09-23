"""Compare an isolated pinned INT8 NLI candidate with the active FP32 model.

This diagnostic never changes the model manifest, active weights, or detector
configuration. It retains synthetic fixture text in its local report only.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys
import time

os.environ["ORT_DISABLE_TELEMETRY"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.engine import Engine
from app.detectors.registry import DetectorRegistry
from app.nlp.model_runtime import file_sha256
from scripts.evaluate_semantic import character_outcome, key, summary, timing


class CandidateRuntime:
    """Process-local, sequential session selection; no persistent changes."""

    def __init__(self, runtime, session):
        self.runtime = runtime
        self.session = session
        self.calls = []

    def ner(self, text):
        return self.runtime.ner(text)

    def nli(self, pairs):
        previous = self.runtime._sessions["context"]
        self.runtime._sessions["context"] = self.session
        started = time.perf_counter()
        try:
            scores = self.runtime.nli(pairs)
        finally:
            self.runtime._sessions["context"] = previous
        self.calls.append({"pairs": pairs, "scores": scores,
                           "elapsed_ms": (time.perf_counter() - started) * 1000})
        return scores


def public_decisions(scores):
    entailment, neutral, contradiction = scores
    return {
        "ordinary": entailment >= .80 and entailment - max(neutral, contradiction) >= .50,
        "explicit_education": entailment >= .55 and entailment - max(neutral, contradiction) >= .10,
    }


def run(candidate, iterations):
    import onnxruntime as ort
    ort.disable_telemetry_events()
    provenance = json.loads(candidate.with_name("provenance.json").read_text())
    assert candidate.stat().st_size == provenance["bytes"]
    assert file_sha256(candidate) == provenance["sha256"]
    tracked = ["app/nlp/model_runtime.py", "app/detectors/semantic.py",
               "app/core/engine.py", "configs/detectors.yaml", "configs/semantic-models.json"]
    source_hashes = {name: file_sha256(ROOT / name) for name in tracked}
    registry = DetectorRegistry.from_config(str(ROOT / "configs/detectors.yaml"))
    semantic = next(detector for detector in registry.detectors if detector.detector_id == "semantic_context")
    original_runtime = semantic.runtime
    options = ort.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.log_severity_level = 4
    started = time.perf_counter()
    candidate_session = ort.InferenceSession(str(candidate), sess_options=options,
                                             providers=["CPUExecutionProvider"])
    load_ms = (time.perf_counter() - started) * 1000
    runtimes = {
        "fp32": CandidateRuntime(original_runtime, original_runtime._sessions["context"]),
        "int8": CandidateRuntime(original_runtime, candidate_session),
    }
    sanity_pairs = [
        ("The man is sleeping.", "The man is sleeping.", "entailment"),
        ("The man is sleeping.", "The man is not sleeping.", "contradiction"),
        ("Мужчина спит.", "Мужчина спит.", "entailment"),
        ("Мужчина спит.", "Мужчина не спит.", "contradiction"),
        ("Мария Кюри изучала радиоактивность.", "В тексте говорится о науке.", "entailment"),
        ("Покупатель сообщил номер своего паспорта.", "Это учебный текст.", "neutral"),
    ]
    sanity = {}
    labels = ("entailment", "neutral", "contradiction")
    for name, runtime in runtimes.items():
        outputs = runtime.nli([(p, h) for p, h, _ in sanity_pairs])
        sanity[name] = [{"premise": p, "hypothesis": h, "expected": e,
                         "scores": s, "argmax": labels[max(range(3), key=lambda i: s[i])]}
                        for (p, h, e), s in zip(sanity_pairs, outputs)]

    engine = Engine(registry.detectors)
    fixture_paths = [ROOT / "tests/fixtures/semantic_context_holdout.json",
                     ROOT / "tests/fixtures/semantic_final_holdout.json"]
    fixture_hashes = {str(path.relative_to(ROOT)): file_sha256(path) for path in fixture_paths}
    cases = [dict(case, fixture=path.name) for path in fixture_paths
             for case in json.loads(path.read_text())["cases"]]
    reports = {name: [] for name in runtimes}
    comparisons = []
    try:
        for name, runtime in runtimes.items():
            semantic.runtime = runtime
            for iteration in range(2):
                engine.mask("Мария Кюри изучала радиоактивность. Клиент Иван Петров сообщил телефон +7 900 111-22-33.",
                            "quantization-warmup", f"{name}-{iteration}")
        for case_index, case in enumerate(cases):
            pair_runs = {}
            variant_order = ("fp32", "int8") if case_index % 2 == 0 else ("int8", "fp32")
            for name in variant_order:
                runtime = runtimes[name]
                semantic.runtime = runtime
                mask_times, unmask_times, nli_times, predictions = [], [], [], []
                all_roundtrips = True
                for iteration in range(iterations):
                    runtime.calls.clear()
                    started = time.perf_counter()
                    result = engine.mask(case["text"], "quantization-diagnostic", f"{name}-{case_index}-{iteration}")
                    mask_times.append((time.perf_counter() - started) * 1000)
                    started = time.perf_counter()
                    restored = engine.unmask(result.masked_text, result.token_mapping)
                    unmask_times.append((time.perf_counter() - started) * 1000)
                    all_roundtrips &= restored == case["text"]
                    nli_times.append(sum(call["elapsed_ms"] for call in runtime.calls))
                    predictions.append([{"category": item.category, "start": item.span.start,
                                         "end": item.span.end, "text": case["text"][item.span.start:item.span.end]}
                                        for item in result.resolved_spans])
                    if iteration == 0:
                        pair_runs[name] = [{"pair": pair, "scores": scores}
                                           for call in runtime.calls
                                           for pair, scores in zip(call["pairs"], call["scores"])]
                predicted = predictions[0]
                gold_keys, predicted_keys = set(map(key, case["gold"])), set(map(key, predicted))
                reports[name].append({**case, "predicted": predicted,
                                      "missing": [s for s in case["gold"] if key(s) not in predicted_keys],
                                      "unexpected": [s for s in predicted if key(s) not in gold_keys],
                                      "exact": gold_keys == predicted_keys,
                                      "stable_across_iterations": all(p == predicted for p in predictions),
                                      "roundtrip_ok": all_roundtrips, "mask_ms": mask_times,
                                      "unmask_ms": unmask_times, "nli_ms": nli_times,
                                      **character_outcome(case["text"], case["gold"], predicted)})
            a, b = reports["fp32"][-1], reports["int8"][-1]
            deltas = []
            # Semantic short-circuiting may ask different later hypotheses.
            # Compare the union outside mask timings without changing policy.
            pair_counts = {name: len(rows) for name, rows in pair_runs.items()}
            paired = {name: {tuple(r["pair"]): r["scores"] for r in rows}
                      for name, rows in pair_runs.items()}
            all_pairs = list(dict.fromkeys(pair for rows in paired.values() for pair in rows))
            for name, scores in paired.items():
                absent = [pair for pair in all_pairs if pair not in scores]
                if absent:
                    scores.update(zip(absent, runtimes[name].nli(absent)))
            for pair in all_pairs:
                fp = {"pair": pair, "scores": paired["fp32"][pair]}
                quantized = {"pair": pair, "scores": paired["int8"][pair]}
                if public_decisions(fp["scores"]) != public_decisions(quantized["scores"]):
                    deltas.append({"pair": fp["pair"], "fp32": fp["scores"], "int8": quantized["scores"],
                                   "fp32_decisions": public_decisions(fp["scores"]),
                                   "int8_decisions": public_decisions(quantized["scores"])})
            comparisons.append({"id": case["id"], "fixture": case["fixture"],
                                "spans_identical": a["predicted"] == b["predicted"],
                                "int8_additional_missed_characters": b["missing_sensitive_characters"] - a["missing_sensitive_characters"],
                                "int8_additional_overmasked_characters": b["unexpected_sensitive_characters"] - a["unexpected_sensitive_characters"],
                                "nli_pairs": pair_counts, "threshold_changes": deltas})
    finally:
        semantic.runtime = original_runtime
    report = {
        "note": "LOCAL SYNTHETIC REGRESSION COMPARISON. No accuracy or RPS guarantee. Candidate isolated; active files unchanged.",
        "candidate": provenance, "candidate_session_load_ms": load_ms,
        "python": platform.python_version(), "platform": platform.platform(),
        "onnxruntime": ort.__version__, "threads": 1, "iterations": iterations,
        "fixture_sha256": fixture_hashes, "source_sha256": source_hashes,
        "source_unchanged_during_run": all(file_sha256(ROOT / name) == digest for name, digest in source_hashes.items()),
        "sanity": sanity, "comparisons": comparisons,
        "results": reports,
        "summary": {name: {
            "required": summary([r for r in rows if r["tier"] == "required"]),
            "diagnostic": summary([r for r in rows if r["tier"] == "diagnostic"]),
            "nli_only": timing([value for row in rows for value in row["nli_ms"] if value > 0]),
        } for name, rows in reports.items()},
    }
    report["recommendation"] = "reject" if any(
        c["int8_additional_missed_characters"] > 0 or c["int8_additional_overmasked_characters"] > 0
        for c in comparisons
    ) else "no_regression_on_these_fixtures_only"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=ROOT / "models/semantic/candidates/mdeberta-int8/model_quantized.onnx")
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/nli-quantization-comparison.json")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    report = run(args.candidate, args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"recommendation": report["recommendation"], "summary": report["summary"],
                      "spans_identical": sum(c["spans_identical"] for c in report["comparisons"]),
                      "cases": len(report["comparisons"]),
                      "source_unchanged_during_run": report["source_unchanged_during_run"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
