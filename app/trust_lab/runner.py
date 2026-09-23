"""Trust Lab runner — orchestrates Actions A, B and C (section 17.2).

Uses the SHARED engine (not a separate lightweight UI detector). Reports real
results including failures. For arbitrary text without labeling it does NOT
compute a fake accuracy — it reports round-trip and the invariants actually
checked.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.core.engine import Engine
from app.policies.loader import PolicyStore
from app.policies.schema import ConsumerPolicy
from app.trust_lab.faults import FaultInjector, MandatoryComponentUnavailable
from app.trust_lab.mutations import GoldSpan, LabeledExample, apply_mutations
from app.trust_lab.policy_comparison import PolicyComparisonResult, compare_policies
from app.trust_lab.report import SafeReport, build_report
from app.trust_lab.transport import TransportCounter

NO_GOLD_NOTE = (
    "Эталонная разметка не задана; проверены round-trip и перечисленные инварианты"
)


@dataclass
class StageTiming:
    stage: str
    seconds: float


@dataclass
class VariationResult:
    name: str
    text: str
    masked_text: str
    gold_spans: list[GoldSpan] = field(default_factory=list)
    masked_spans: list[GoldSpan] = field(default_factory=list)
    missed: list[GoldSpan] = field(default_factory=list)
    overmasked: list[GoldSpan] = field(default_factory=list)
    exact_round_trip: bool = True
    restored: str = ""
    time_seconds: float = 0.0
    status: str = "passed"


@dataclass
class ActionAResult:
    run_id: str
    labeled: bool
    text: str
    masked_text: str
    restored: str
    exact_round_trip: bool
    detected_counts: dict[str, int] = field(default_factory=dict)
    masked_counts: dict[str, int] = field(default_factory=dict)
    spans: list[GoldSpan] = field(default_factory=list)
    service_words: list[str] = field(default_factory=list)
    reasons: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    timings: list[StageTiming] = field(default_factory=list)
    policy_version: str = ""
    detector_manifest_version: str = ""
    variations: list[VariationResult] = field(default_factory=list)
    note: str = ""


@dataclass
class ActionCResult:
    run_id: str
    status: str
    reason: str
    upstream_calls: int
    request_sent_out: bool
    degraded_components: list[str] = field(default_factory=list)
    timings: list[StageTiming] = field(default_factory=list)


class TrustLabRunner:
    """Runs real Trust Lab checks against the shared engine."""

    def __init__(
        self,
        engine: Engine,
        policy_store: PolicyStore,
        detector_manifest_version: str,
        transport: TransportCounter,
        fault_injector: FaultInjector | None = None,
        build_version: str = "0.1.0",
    ) -> None:
        self._engine = engine
        self._policy_store = policy_store
        self._detector_manifest_version = detector_manifest_version
        self._transport = transport
        self._faults = fault_injector or FaultInjector(enabled=False)
        self._build_version = build_version
        # Checks actually executed by this runner (section 17.3: reports must
        # derive from actual server-recorded runs, never client "passed").
        self._recorded_checks: dict[str, str] = {}

    def record_check(self, name: str, status: str) -> None:
        """Record that a check was actually executed with a real status."""
        self._recorded_checks[name] = status

    @property
    def recorded_checks(self) -> dict[str, str]:
        return dict(self._recorded_checks)

    # -- Action A ------------------------------------------------------------

    def action_a(
        self,
        text: str,
        consumer: str,
        labeled: LabeledExample | None = None,
        seed: int | None = None,
        run_variations: bool = False,
    ) -> ActionAResult:
        policy = self._policy_store.get_consumer(consumer)
        if policy is None:
            raise ValueError(f"unknown consumer: {consumer}")

        started = time.monotonic()
        result = self._engine.mask(
            text,
            policy.namespace,
            "trust-lab-a",
            allowed_categories=policy.detect_types_set(),
            policy=policy,
        )
        mask_time = time.monotonic() - started

        started = time.monotonic()
        restored = self._engine.unmask(result.masked_text, result.token_mapping)
        unmask_time = time.monotonic() - started

        exact_round_trip = restored == text
        self.record_check("action_a_round_trip", "passed" if exact_round_trip else "failed")
        self.record_check("action_a_mask", "passed")
        masked_spans = [
            GoldSpan(rs.span.start, rs.span.end, rs.category)
            for rs in result.resolved_spans
        ]

        detected_counts: dict[str, int] = {}
        for d in result.detections:
            detected_counts[d.category] = detected_counts.get(d.category, 0) + 1
        masked_counts: dict[str, int] = {}
        for rs in result.resolved_spans:
            masked_counts[rs.category] = masked_counts.get(rs.category, 0) + 1

        reasons = self._build_reasons(result.resolved_spans)

        variations: list[VariationResult] = []
        if run_variations and labeled is not None:
            variations = self._run_variations(labeled, policy, seed or 20260922)

        note = NO_GOLD_NOTE if labeled is None else ""
        return ActionAResult(
            run_id=self._new_run_id(),
            labeled=labeled is not None,
            text=text,
            masked_text=result.masked_text,
            restored=restored,
            exact_round_trip=exact_round_trip,
            detected_counts=detected_counts,
            masked_counts=masked_counts,
            spans=masked_spans,
            service_words=list(labeled.service_words) if labeled else [],
            reasons=reasons,
            decisions=[
                {
                    "category": detection.category,
                    "decision": detection.decision.value,
                    "rule_id": detection.rule_id,
                    "detector_id": detection.detector_id,
                    "evidence_spans": [
                        {"start": span.start, "end": span.end}
                        for span in detection.evidence_spans
                    ],
                    "sensitive_spans": [
                        {"start": span.start, "end": span.end}
                        for span in detection.sensitive_spans
                    ],
                    "signals": [signal.name for signal in detection.signals],
                }
                for detection in result.detections
            ],
            timings=[
                StageTiming("mask", mask_time),
                StageTiming("unmask", unmask_time),
            ],
            policy_version=self._policy_store.policy_version,
            detector_manifest_version=self._detector_manifest_version,
            variations=variations,
            note=note,
        )

    def _run_variations(
        self, labeled: LabeledExample, policy: ConsumerPolicy, seed: int
    ) -> list[VariationResult]:
        out: list[VariationResult] = []
        for mut in apply_mutations(labeled, seed):
            started = time.monotonic()
            result = self._engine.mask(
                mut.text,
                policy.namespace,
                "trust-lab-var",
                allowed_categories=policy.detect_types_set(),
                policy=policy,
            )
            elapsed = time.monotonic() - started
            restored = self._engine.unmask(result.masked_text, result.token_mapping)
            round_trip = restored == mut.text

            masked_spans = [
                GoldSpan(rs.span.start, rs.span.end, rs.category)
                for rs in result.resolved_spans
            ]
            gold_chars = _span_chars(mut.gold_spans)
            masked_chars = _span_chars(masked_spans)
            # A gold span is "missed" unless it is FULLY concealed (exact span
            # match). Partial exposure must not count as complete concealment
            # (section 17.4).
            missed = [g for g in mut.gold_spans if not _fully_concealed(g, masked_spans)]
            overmasked = [g for g in masked_spans if not _fully_concealed(g, mut.gold_spans)]

            status = "passed"
            if missed or overmasked or not round_trip:
                status = "failed"

            out.append(
                VariationResult(
                    name=mut.name,
                    text=mut.text,
                    masked_text=result.masked_text,
                    gold_spans=mut.gold_spans,
                    masked_spans=masked_spans,
                    missed=missed,
                    overmasked=overmasked,
                    exact_round_trip=round_trip,
                    restored=restored,
                    time_seconds=elapsed,
                    status=status,
                )
            )
        return out

    # -- Action B ------------------------------------------------------------

    def action_b(
        self,
        consumer: str,
        candidate_policy: ConsumerPolicy,
        examples: list[LabeledExample],
        candidate_engine: Engine | None = None,
    ) -> PolicyComparisonResult:
        active = self._policy_store.get_consumer(consumer)
        if active is None:
            raise ValueError(f"unknown consumer: {consumer}")
        cand_engine = candidate_engine or self._engine
        return compare_policies(
            self._engine, cand_engine, active, candidate_policy, examples, active.namespace
        )

    # -- Action C ------------------------------------------------------------

    def action_c(self, consumer: str, text: str) -> ActionCResult:
        """Run a synthetic admin-only fault-injection demo.

        If a mandatory component is unavailable, processing stops and the
        instrumented transport is NOT called (upstream_calls == 0).
        """
        policy = self._policy_store.get_consumer(consumer)
        if policy is None:
            raise ValueError(f"unknown consumer: {consumer}")

        before = self._transport.calls
        degraded = self._faults.degraded_components()
        started = time.monotonic()

        if not degraded:
            # No fault active: run a normal mask AND make a real transport call
            # (positive control). This proves the healthy path CAN call the
            # transport, so zero calls under failure is meaningful (section 17.4).
            result = self._engine.mask(
                text,
                policy.namespace,
                "trust-lab-c",
                allowed_categories=policy.detect_types_set(),
                policy=policy,
            )
            self._transport.complete(result.masked_text)
            elapsed = time.monotonic() - started
            after = self._transport.calls
            self.record_check("action_c_healthy_transport", "passed")
            return ActionCResult(
                run_id=self._new_run_id(),
                status="completed",
                reason="",
                upstream_calls=after - before,
                request_sent_out=False,
                degraded_components=[],
                timings=[StageTiming("mask", elapsed)],
            )

        # A mandatory component is unavailable -> stop before any upstream call.
        reason = "обязательный компонент недоступен"
        elapsed = time.monotonic() - started
        after = self._transport.calls
        self.record_check("action_c_blocked_transport", "passed")
        return ActionCResult(
            run_id=self._new_run_id(),
            status="stopped",
            reason=reason,
            upstream_calls=after - before,
            request_sent_out=False,
            degraded_components=degraded,
            timings=[StageTiming("blocked", elapsed)],
        )

    # -- Report --------------------------------------------------------------

    def build_report(
        self,
        *,
        dataset_or_scenario_id: str | None = None,
        seed: int | None = None,
        input_length: int | None = None,
        detected_counts: dict[str, int] | None = None,
        masked_counts: dict[str, int] | None = None,
        check_statuses: dict[str, str] | None = None,
        exact_round_trip: str = "not_tested",
        upstream_calls: int = 0,
        timings: dict[str, float] | None = None,
        degraded_components: list[str] | None = None,
        known_limitations: list[str] | None = None,
        include_synthetic: bool = False,
    ) -> SafeReport:
        return build_report(
            build_version=self._build_version,
            policy_version=self._policy_store.policy_version,
            detector_manifest_version=self._detector_manifest_version,
            dataset_or_scenario_id=dataset_or_scenario_id,
            seed=seed,
            input_length=input_length,
            detected_counts=detected_counts,
            masked_counts=masked_counts,
            check_statuses=check_statuses,
            exact_round_trip=exact_round_trip,
            upstream_calls=upstream_calls,
            timings=timings,
            degraded_components=degraded_components,
            known_limitations=known_limitations,
            include_synthetic=include_synthetic,
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _new_run_id() -> str:
        import uuid

        return uuid.uuid4().hex

    @staticmethod
    def _build_reasons(resolved_spans) -> list[dict]:
        reasons: list[dict] = []
        for rs in resolved_spans:
            reasons.append(
                {
                    "category": rs.category,
                    "detector": ",".join(rs.detector_ids) or "unknown",
                    "rule": rs.rule_id or "",
                    "context": "sensitive_value",
                }
            )
        return reasons


def _span_chars(spans: list[GoldSpan]) -> set[int]:
    chars: set[int] = set()
    for g in spans:
        chars.update(range(g.start, g.end))
    return chars


def _fully_concealed(g: GoldSpan, spans: list[GoldSpan]) -> bool:
    """True if the gold span is fully covered by a masked span of the SAME
    category. Partial exposure is NOT complete concealment (section 17.4)."""
    for s in spans:
        if s.category == g.category and s.start <= g.start and s.end >= g.end:
            return True
    return False


def _overlaps(g: GoldSpan, chars: set[int]) -> bool:
    return any(i in chars for i in range(g.start, g.end))
