"""Trust Lab API endpoints (section 17).

- POST /trust-lab/action-a  — «Проверить на новом тексте»
- POST /trust-lab/action-b  — «Проверить изменение политики до включения»
- POST /trust-lab/action-c  — «Проверить отказ» (admin-only fault injection)
- POST /trust-lab/action-c/recover — clear injected faults (admin-only)
- POST /trust-lab/report    — «Паспорт проверки» (safe report, no raw PII)

Fault injection is available ONLY when PII_TRUST_LAB_FAULTS=1 and requires the
admin key (PII_TRUST_LAB_ADMIN_KEY). It is disabled in the normal evaluator
profile and cannot be enabled by user text (R52).
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.api.errors import forbidden, unauthorized
from app.core.engine import Engine
from app.detectors.registry import DetectorRegistry
from app.policies.loader import PolicyStore
from app.policies.schema import ConsumerPolicy
from app.security.auth import verify_api_key
from app.trust_lab.faults import FaultInjector, FaultInjectionDisabled
from app.trust_lab.mutations import GoldSpan, LabeledExample
from app.trust_lab.regression import (
    REGRESSION_SEED,
    REGRESSION_SET_ID,
    build_regression_set,
)
from app.trust_lab.runner import TrustLabRunner
from app.trust_lab.simple_detector import SyntheticClientIdDetector
from app.trust_lab.transport import TransportCounter

_ADMIN_KEY_ENV = "PII_TRUST_LAB_ADMIN_KEY"
_FAULTS_ENV = "PII_TRUST_LAB_FAULTS"


def _faults_enabled() -> bool:
    return os.environ.get(_FAULTS_ENV, "").strip() == "1"


def _verify_admin(x_admin_key: str | None) -> None:
    expected = os.environ.get(_ADMIN_KEY_ENV, "").strip()
    if not expected or not x_admin_key or x_admin_key != expected:
        raise unauthorized()


def _verify_consumer_auth(policy: ConsumerPolicy, x_api_key: str | None) -> bool:
    """Verify a consumer's API key. The competition_exception (autocheck) needs
    no key; protected consumers do (section 17.4: helper APIs respect access)."""
    if policy.authentication == "competition_exception":
        return True
    return verify_api_key(policy, x_api_key)


# -- request models ----------------------------------------------------------

class ActionARequest(BaseModel):
    text: str
    consumer: str = "support_demo"
    labeled: bool = False
    seed: int | None = None
    run_variations: bool = False


class CandidateSpec(BaseModel):
    default_action: str | None = None
    add_detector: bool = False
    detect_types: str | None = None


class ActionBRequest(BaseModel):
    consumer: str = "support_demo"
    candidate: CandidateSpec = Field(default_factory=CandidateSpec)


class ActionCRequest(BaseModel):
    consumer: str = "support_demo"
    text: str
    fault_type: str = "detector"  # "detector" | "vault"
    detector_id: str = "full_name"


class ReportRequest(BaseModel):
    dataset_or_scenario_id: str | None = None
    seed: int | None = None
    input_length: int | None = None
    detected_counts: dict[str, int] = Field(default_factory=dict)
    masked_counts: dict[str, int] = Field(default_factory=dict)
    check_statuses: dict[str, str] = Field(default_factory=dict)
    exact_round_trip: str = "not_tested"
    upstream_calls: int = 0
    timings: dict[str, float] = Field(default_factory=dict)
    degraded_components: list[str] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)
    include_synthetic: bool = False


# -- default labeled example for Action A ------------------------------------

def _labeled_from_text(
    engine: Engine, text: str, consumer: str, policy_store: PolicyStore
) -> LabeledExample:
    """Derive a labeled example from the submitted text using the shared engine.

    The gold spans are the engine's detected spans (marked as derived, not
    independent ground truth). This ensures Action A variations use the
    submitted input rather than a built-in example (section 17.4).
    """
    policy = policy_store.get_consumer(consumer)
    if policy is None:
        raise forbidden()
    result = engine.mask(
        text,
        policy.namespace,
        "trust-lab-a",
        allowed_categories=policy.detect_types_set(),
        policy=policy,
    )
    spans = [
        GoldSpan(rs.span.start, rs.span.end, rs.category)
        for rs in result.resolved_spans
    ]
    return LabeledExample(
        text=text,
        gold_spans=spans,
        service_words=[],
    )


def _build_candidate_policy(
    active: ConsumerPolicy, spec: CandidateSpec
) -> ConsumerPolicy:
    data = active.model_dump()
    if spec.default_action:
        data["default_action"] = spec.default_action
    if spec.detect_types:
        data["detect_types"] = spec.detect_types
    if spec.add_detector:
        types = set(active.detect_types_set())
        types.add("SYNTHETIC_CLIENT_ID")
        data["detect_types"] = ",".join(sorted(types))
    data["name"] = "candidate"
    return ConsumerPolicy.model_validate(data)


def build_trust_lab_router(
    engine: Engine,
    policy_store: PolicyStore,
    registry: DetectorRegistry,
    transport: TransportCounter,
    fault_injector: FaultInjector,
    detector_manifest_version: str,
    build_version: str = "0.1.0",
) -> APIRouter:
    router = APIRouter(prefix="/trust-lab", tags=["trust-lab"])
    runner = TrustLabRunner(
        engine,
        policy_store,
        detector_manifest_version,
        transport,
        fault_injector,
        build_version=build_version,
    )

    @router.post("/action-a")
    def action_a(req: ActionARequest, x_api_key: str | None = Header(default=None)):
        # Protected consumers require authentication (section 17.4: helper APIs
        # must respect consumer access). analytics_demo (unmask=false) must not
        # return a newly submitted original.
        policy = policy_store.get_consumer(req.consumer)
        if policy is None or not policy.enabled:
            raise forbidden()
        if not _verify_consumer_auth(policy, x_api_key):
            raise unauthorized()
        # Selected-input checks must use the submitted input (section 17.4).
        # When labeled=True, derive gold spans from the submitted text via the
        # shared engine (marked as derived, not independent ground truth).
        labeled = None
        if req.labeled:
            labeled = _labeled_from_text(engine, req.text, req.consumer, policy_store)
        result = runner.action_a(
            req.text,
            req.consumer,
            labeled=labeled,
            seed=req.seed,
            run_variations=req.run_variations,
        )
        return _action_a_dict(result)

    @router.post("/action-b")
    def action_b(req: ActionBRequest):
        active = policy_store.get_consumer(req.consumer)
        if active is None:
            raise forbidden()
        candidate = _build_candidate_policy(active, req.candidate)
        candidate_engine = None
        if req.candidate.add_detector:
            candidate_engine = Engine(registry.detectors + [SyntheticClientIdDetector()])
        result = runner.action_b(
            req.consumer, candidate, build_regression_set(), candidate_engine
        )
        return _action_b_dict(result)

    @router.post("/action-c")
    def action_c(req: ActionCRequest, x_admin_key: str | None = Header(default=None)):
        _verify_admin(x_admin_key)
        if not _faults_enabled():
            raise forbidden()
        try:
            if req.fault_type == "vault":
                fault_injector.set_vault_down(True)
            else:
                fault_injector.set_detector_down(req.detector_id, True)
        except FaultInjectionDisabled:
            raise forbidden()
        result = runner.action_c(req.consumer, req.text)
        return _action_c_dict(result)

    @router.post("/action-c/recover")
    def action_c_recover(x_admin_key: str | None = Header(default=None)):
        _verify_admin(x_admin_key)
        if not _faults_enabled():
            raise forbidden()
        try:
            fault_injector.reset()
        except FaultInjectionDisabled:
            raise forbidden()
        return {"status": "recovered", "degraded_components": fault_injector.degraded_components()}

    @router.post("/report")
    def report(req: ReportRequest):
        # Reports must derive from actual server-recorded runs, never client
        # "passed" for a check that never executed (section 17.3/17.4). We
        # ignore client-supplied check_statuses and use only recorded checks.
        safe = runner.build_report(
            dataset_or_scenario_id=req.dataset_or_scenario_id,
            seed=req.seed,
            input_length=req.input_length,
            detected_counts=req.detected_counts,
            masked_counts=req.masked_counts,
            check_statuses=runner.recorded_checks,
            exact_round_trip=req.exact_round_trip,
            upstream_calls=req.upstream_calls,
            timings=req.timings,
            degraded_components=req.degraded_components,
            known_limitations=req.known_limitations,
            include_synthetic=req.include_synthetic,
        )
        return safe.to_dict()

    return router


# -- serializers -------------------------------------------------------------

def _action_a_dict(result) -> dict:
    return {
        "run_id": result.run_id,
        "labeled": result.labeled,
        "text": result.text,
        "masked_text": result.masked_text,
        "restored": result.restored,
        "exact_round_trip": result.exact_round_trip,
        "detected_counts": result.detected_counts,
        "masked_counts": result.masked_counts,
        "spans": [_span_dict(s) for s in result.spans],
        "service_words": result.service_words,
        "reasons": result.reasons,
        "timings": [{"stage": t.stage, "seconds": t.seconds} for t in result.timings],
        "policy_version": result.policy_version,
        "detector_manifest_version": result.detector_manifest_version,
        "variations": [_variation_dict(v) for v in result.variations],
        "note": result.note,
    }


def _variation_dict(v) -> dict:
    return {
        "name": v.name,
        "text": v.text,
        "masked_text": v.masked_text,
        "gold_spans": [_span_dict(s) for s in v.gold_spans],
        "masked_spans": [_span_dict(s) for s in v.masked_spans],
        "missed": [_span_dict(s) for s in v.missed],
        "overmasked": [_span_dict(s) for s in v.overmasked],
        "exact_round_trip": v.exact_round_trip,
        "restored": v.restored,
        "time_seconds": v.time_seconds,
        "status": v.status,
    }


def _span_dict(s: GoldSpan) -> dict:
    return {"start": s.start, "end": s.end, "category": s.category}


def _action_b_dict(result) -> dict:
    return {
        "active": _policy_summary_dict(result.active),
        "candidate": _policy_summary_dict(result.candidate),
        "delta_missed": result.delta_missed,
        "delta_overmasked": result.delta_overmasked,
        "delta_round_trip_failures": result.delta_round_trip_failures,
        "delta_time_seconds": result.delta_time_seconds,
        "regressions": result.regressions,
        "improvements": result.improvements,
        "note": result.note,
    }


def _policy_summary_dict(s) -> dict:
    return {
        "policy_version": s.policy_version,
        "mask_action": s.mask_action,
        "examples": s.examples,
        "missed_entities": s.missed_entities,
        "overmasked_entities": s.overmasked_entities,
        "round_trip_failures": s.round_trip_failures,
        "total_time_seconds": s.total_time_seconds,
    }


def _action_c_dict(result) -> dict:
    return {
        "run_id": result.run_id,
        "status": result.status,
        "reason": result.reason,
        "upstream_calls": result.upstream_calls,
        "request_sent_out": result.request_sent_out,
        "degraded_components": result.degraded_components,
        "timings": [{"stage": t.stage, "seconds": t.seconds} for t in result.timings],
    }
