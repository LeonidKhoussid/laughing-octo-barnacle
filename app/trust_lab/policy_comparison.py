"""Compare the active policy vs a candidate policy on a labeled regression set.

Shows changes in misses / overmasks / round-trip / time. It does NOT claim the
candidate is "safe for any text" — only the specific tests run are reported
(R68, section 17.2 Action B). Regressions are shown, not hidden.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.core.engine import Engine
from app.policies.schema import ConsumerPolicy
from app.trust_lab.mutations import GoldSpan, LabeledExample


@dataclass
class PolicyRunSummary:
    """Aggregate result of running one policy over the regression set."""

    policy_version: str
    mask_action: str
    examples: int = 0
    missed_entities: int = 0
    overmasked_entities: int = 0
    round_trip_failures: int = 0
    total_time_seconds: float = 0.0
    per_example: list = field(default_factory=list)


@dataclass
class ExampleRun:
    """Per-example result for one policy."""

    example_index: int
    missed: list[GoldSpan] = field(default_factory=list)
    overmasked: list[GoldSpan] = field(default_factory=list)
    round_trip: bool = True
    time_seconds: float = 0.0


@dataclass
class PolicyComparisonResult:
    """Result of comparing active vs candidate policy."""

    active: PolicyRunSummary
    candidate: PolicyRunSummary
    delta_missed: int
    delta_overmasked: int
    delta_round_trip_failures: int
    delta_time_seconds: float
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    note: str = ""


def _run_policy(
    engine: Engine,
    policy: ConsumerPolicy,
    examples: list[LabeledExample],
    namespace: str,
) -> PolicyRunSummary:
    summary = PolicyRunSummary(
        policy_version=policy.name or "candidate",
        mask_action=policy.default_action,
        examples=len(examples),
    )
    for idx, ex in enumerate(examples):
        started = time.monotonic()
        result = engine.mask(
            ex.text,
            namespace,
            f"trust-lab-{idx}",
            allowed_categories=policy.detect_types_set(),
            policy=policy,
        )
        elapsed = time.monotonic() - started

        gold_chars = _span_chars(ex.gold_spans)
        masked_spans = [
            GoldSpan(rs.span.start, rs.span.end, rs.category)
            for rs in result.resolved_spans
        ]
        masked_chars = _span_chars(masked_spans)

        missed = _missed_spans(ex.gold_spans, masked_chars)
        overmasked = _overmasked_spans(masked_spans, gold_chars)

        restored = engine.unmask(result.masked_text, result.token_mapping)
        round_trip = restored == ex.text

        summary.missed_entities += len(missed)
        summary.overmasked_entities += len(overmasked)
        if not round_trip:
            summary.round_trip_failures += 1
        summary.total_time_seconds += elapsed
        summary.per_example.append(
            ExampleRun(
                example_index=idx,
                missed=missed,
                overmasked=overmasked,
                round_trip=round_trip,
                time_seconds=elapsed,
            )
        )
    return summary


def _span_chars(spans: list[GoldSpan]) -> set[int]:
    chars: set[int] = set()
    for g in spans:
        chars.update(range(g.start, g.end))
    return chars


def _missed_spans(gold: list[GoldSpan], masked_chars: set[int]) -> list[GoldSpan]:
    missed: list[GoldSpan] = []
    for g in gold:
        if not any(i in masked_chars for i in range(g.start, g.end)):
            missed.append(g)
    return missed


def _overmasked_spans(masked: list[GoldSpan], gold_chars: set[int]) -> list[GoldSpan]:
    over: list[GoldSpan] = []
    for g in masked:
        if not any(i in gold_chars for i in range(g.start, g.end)):
            over.append(g)
    return over


def compare_policies(
    active_engine: Engine,
    candidate_engine: Engine,
    active_policy: ConsumerPolicy,
    candidate_policy: ConsumerPolicy,
    examples: list[LabeledExample],
    namespace: str,
) -> PolicyComparisonResult:
    """Run active and candidate policies on the same labeled regression set.

    active_engine and candidate_engine may differ (e.g. the candidate adds a
    detector). Both policies run on the SAME labeled examples.
    """
    active = _run_policy(active_engine, active_policy, examples, namespace)
    candidate = _run_policy(candidate_engine, candidate_policy, examples, namespace)

    delta_missed = candidate.missed_entities - active.missed_entities
    delta_overmasked = candidate.overmasked_entities - active.overmasked_entities
    delta_rt = candidate.round_trip_failures - active.round_trip_failures
    delta_time = candidate.total_time_seconds - active.total_time_seconds

    regressions: list[str] = []
    improvements: list[str] = []
    if delta_missed > 0:
        regressions.append(f"missed entities +{delta_missed}")
    elif delta_missed < 0:
        improvements.append(f"missed entities {delta_missed}")
    if delta_overmasked > 0:
        regressions.append(f"overmasked entities +{delta_overmasked}")
    elif delta_overmasked < 0:
        improvements.append(f"overmasked entities {delta_overmasked}")
    if delta_rt > 0:
        regressions.append(f"round-trip failures +{delta_rt}")
    elif delta_rt < 0:
        improvements.append(f"round-trip failures {delta_rt}")
    if delta_time > 0.0005:
        regressions.append(f"time +{delta_time:.4f}s")
    elif delta_time < -0.0005:
        improvements.append(f"time {delta_time:.4f}s")

    note = (
        "Проверены только конкретные тесты из размеченного набора. "
        "Результат не является доказательством безопасности для любых текстов."
    )
    return PolicyComparisonResult(
        active=active,
        candidate=candidate,
        delta_missed=delta_missed,
        delta_overmasked=delta_overmasked,
        delta_round_trip_failures=delta_rt,
        delta_time_seconds=delta_time,
        regressions=regressions,
        improvements=improvements,
        note=note,
    )
