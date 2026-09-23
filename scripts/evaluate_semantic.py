"""Evaluate manually labelled public/private scenarios without deriving gold.

Synthetic local diagnostics only; not the organizer score or a representative
accuracy estimate. Diagnostics are always separate from required scenarios.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.engine import Engine  # noqa: E402
from app.detectors.registry import DetectorRegistry  # noqa: E402


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def timing(values: list[float]) -> dict:
    return {
        'observations': len(values),
        'mean_ms': statistics.mean(values) if values else None,
        'p50_ms': percentile(values, .50),
        'p95_ms': percentile(values, .95),
        'p99_ms': percentile(values, .99),
        'max_ms': max(values) if values else None,
    }


def key(span: dict) -> tuple[str, int, int]:
    return span['category'], span['start'], span['end']


def positions(spans: list[dict]) -> set[int]:
    return {position for span in spans for position in range(span['start'], span['end'])}


def character_outcome(text: str, gold: list[dict], predicted: list[dict]) -> dict:
    """Compare sensitive values while allowing harmless whitespace segmentation.

    The organizers allow a field to be concealed as one or multiple spans.
    Whitespace carries no credential value here; every non-whitespace code point
    in annotated values still must be covered, with no extra content concealed.
    Category correctness is reported separately from privacy coverage.
    """
    gold_chars = {p for p in positions(gold) if not text[p].isspace()}
    predicted_chars = {p for p in positions(predicted) if not text[p].isspace()}
    def categorized(spans):
        return {(span['category'], p) for span in spans
                for p in range(span['start'], span['end']) if not text[p].isspace()}
    return {
        'sensitive_character_exact': gold_chars == predicted_chars,
        'category_character_exact': categorized(gold) == categorized(predicted),
        'missing_sensitive_characters': len(gold_chars - predicted_chars),
        'unexpected_sensitive_characters': len(predicted_chars - gold_chars),
    }


def summary(rows: list[dict]) -> dict:
    tp, gold_count, predicted_count = Counter(), Counter(), Counter()
    character_results = [character_outcome(row['text'], row['gold'], row['predicted']) for row in rows]
    concealment, overmask = [], []
    for row in rows:
        gold = set(map(key, row['gold']))
        predicted = set(map(key, row['predicted']))
        tp.update(category for category, _, _ in gold & predicted)
        gold_count.update(category for category, _, _ in gold)
        predicted_count.update(category for category, _, _ in predicted)
        gold_chars = positions(row['gold'])
        predicted_chars = positions(row['predicted'])
        if gold_chars:
            concealment.append(len(gold_chars & predicted_chars) / len(gold_chars))
        non_sensitive_count = len(row['text']) - len(gold_chars)
        if non_sensitive_count:
            overmask.append(len(predicted_chars - gold_chars) / non_sensitive_count)
    per_category = {}
    for category in sorted(gold_count.keys() | predicted_count.keys()):
        g, p, t = gold_count[category], predicted_count[category], tp[category]
        precision = t / p if p else None
        recall = t / g if g else None
        per_category[category] = {
            'gold': g, 'predicted': p, 'true_positive': t,
            'precision': precision, 'recall': recall,
            'f1': 2 * t / (p + g) if p + g else None,
        }
    return {
        'cases': len(rows),
        'exact_cases': sum(row['exact'] for row in rows),
        'exact_case_rate': sum(row['exact'] for row in rows) / len(rows) if rows else None,
        'roundtrip_cases': sum(row['roundtrip_ok'] for row in rows),
        'sensitive_character_exact_cases': sum(result['sensitive_character_exact'] for result in character_results),
        'category_character_exact_cases': sum(result['category_character_exact'] for result in character_results),
        'failed_character_case_ids': [row.get('id', str(i)) for i, (row, result) in enumerate(zip(rows, character_results)) if not result['sensitive_character_exact']],
        'missing_sensitive_characters': sum(result['missing_sensitive_characters'] for result in character_results),
        'unexpected_sensitive_characters': sum(result['unexpected_sensitive_characters'] for result in character_results),
        'missing_exact_spans': sum(len(row['missing']) for row in rows),
        'unexpected_exact_spans': sum(len(row['unexpected']) for row in rows),
        'document_mean_concealment': statistics.mean(concealment) if concealment else None,
        'document_mean_overmask_rate': statistics.mean(overmask) if overmask else None,
        'per_category': per_category,
        'mask_timing': timing([value for row in rows for value in row['mask_ms']]),
        'unmask_timing': timing([value for row in rows for value in row['unmask_ms']]),
    }


def evaluate(fixture_path: Path, config_path: Path, iterations: int = 1) -> dict:
    fixture_bytes = fixture_path.read_bytes()
    dataset = json.loads(fixture_bytes)
    cases = dataset['cases']
    if iterations < 1:
        raise ValueError('iterations must be positive')
    for case in cases:
        assert case['tier'] in {'required', 'diagnostic'}, case['id']
        last_end = 0
        for span in case['gold']:
            assert last_end <= span['start'] < span['end'] <= len(case['text']), case['id']
            assert case['text'][span['start']:span['end']] == span['text'], case['id']
            last_end = span['end']
    started = time.perf_counter()
    registry = DetectorRegistry.from_config(str(config_path))
    engine = Engine(registry.detectors)
    initialization_ms = (time.perf_counter() - started) * 1000
    # Warmup uses an unrelated synthetic record and is excluded from timings.
    for i in range(3):
        warm = engine.mask('Клиент Иван Иванович Петров, телефон +7 900 111-22-33.', 'semantic-warmup', str(i))
        engine.unmask(warm.masked_text, warm.token_mapping)
    rows = []
    for case in cases:
        text = case['text']
        mask_times, unmask_times, all_predictions = [], [], []
        roundtrip_ok = True
        for iteration in range(iterations):
            started = time.perf_counter()
            result = engine.mask(text, 'semantic-evaluation', f"{case['id']}-{iteration}")
            mask_times.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            restored = engine.unmask(result.masked_text, result.token_mapping)
            unmask_times.append((time.perf_counter() - started) * 1000)
            roundtrip_ok = roundtrip_ok and restored == text
            all_predictions.append([
                {'category': resolved.category, 'start': resolved.span.start,
                 'end': resolved.span.end, 'text': text[resolved.span.start:resolved.span.end]}
                for resolved in result.resolved_spans
            ])
        predicted = all_predictions[0]
        stable = all(prediction == predicted for prediction in all_predictions)
        gold_keys, predicted_keys = set(map(key, case['gold'])), set(map(key, predicted))
        rows.append({
            'id': case['id'], 'tier': case['tier'], 'tags': case['tags'],
            'family': case.get('family', 'unspecified'),
            'text': text, 'intent': case['intent'], 'gold': case['gold'],
            'predicted': predicted,
            'missing': [span for span in case['gold'] if key(span) not in predicted_keys],
            'unexpected': [span for span in predicted if key(span) not in gold_keys],
            'exact': gold_keys == predicted_keys and stable,
            'stable_across_iterations': stable,
            **character_outcome(text, case['gold'], predicted),
            'roundtrip_ok': roundtrip_ok,
            'mask_ms': mask_times, 'unmask_ms': unmask_times,
        })
    return {
        'note': 'LOCAL SYNTHETIC DIAGNOSTIC; not representative accuracy or the official competition metric. Reusing failures for fixes makes this a development challenge, not untouched holdout.',
        'fixture_path': str(fixture_path),
        'fixture_sha256': hashlib.sha256(fixture_bytes).hexdigest(),
        'config_path': str(config_path),
        'config_sha256': hashlib.sha256(config_path.read_bytes()).hexdigest(),
        'python': platform.python_version(), 'platform': platform.platform(),
        'detectors': [{'id': d.detector_id, 'version': d.detector_version} for d in registry.detectors],
        'initialization_ms': initialization_ms,
        'character_metric': 'Per-document union of non-whitespace Unicode code points; separately compares category-position pairs. Allows single-field span splitting but does not ignore letters, digits, punctuation, or extra masked text. Strict boundary metrics remain unchanged.',
        'timing_scope': 'Sequential in-process Engine.mask/unmask after three warmups; excludes HTTP, Vault, concurrency, queueing and first model warmup. No RPS/SLA claim.',
        'iterations_per_case': iterations,
        'by_tier': {tier: summary([row for row in rows if row['tier'] == tier]) for tier in ('required', 'diagnostic')},
        'by_family_required': {family: summary([row for row in rows if row['tier'] == 'required' and row['family'] == family]) for family in sorted({row['family'] for row in rows if row['tier'] == 'required'})},
        'results': rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=ROOT / 'tests/fixtures/semantic_context_holdout.json')
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/detectors.yaml')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/semantic_report.json')
    parser.add_argument('--iterations', type=int, default=1)
    parser.add_argument('--gate', action='store_true', help='Exit nonzero if any required case has a span mismatch or any roundtrip fails.')
    args = parser.parse_args()
    report = evaluate(args.fixtures.resolve(), args.config.resolve(), args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for tier, data in report['by_tier'].items():
        print(f"{tier}: span-exact {data['exact_cases']}/{data['cases']}; character-exact {data['sensitive_character_exact_cases']}/{data['cases']}; missing {data['missing_exact_spans']}; unexpected {data['unexpected_exact_spans']}; roundtrip {data['roundtrip_cases']}/{data['cases']}")
    print(f'Report: {args.output}')
    required = report['by_tier']['required']
    failed = required['exact_cases'] != required['cases'] or not all(row['roundtrip_ok'] for row in report['results'])
    return 1 if args.gate and failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
