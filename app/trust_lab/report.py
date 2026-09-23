"""«Паспорт проверки» — a compact safe report without raw PII by default.

The report contains run metadata, check statuses, counts, actual timings and
known limitations. It NEVER contains token values, original text, raw hashes of
personal strings, Vault mapping, or API keys (section 17.3). Synthetic examples
are exportable only in a separately-marked mode (include_synthetic=True).

The report is NOT a cryptographic proof of anonymity, a compliance certificate,
or formal verification.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class SafeReport:
    """A safe report object. Serialized to JSON without raw PII."""

    run_id: str
    created_at: str
    build_version: str
    policy_version: str
    detector_manifest_version: str
    dataset_or_scenario_id: str | None = None
    seed: int | None = None
    input_length: int | None = None
    token_counter_type: str = "unicode_code_points"
    detected_counts: dict[str, int] = field(default_factory=dict)
    masked_counts: dict[str, int] = field(default_factory=dict)
    check_statuses: dict[str, str] = field(default_factory=dict)
    exact_round_trip: str = "not_tested"
    upstream_calls: int = 0
    timings: dict[str, float] = field(default_factory=dict)
    degraded_components: list[str] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)
    include_synthetic: bool = False

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "build_version": self.build_version,
            "policy_version": self.policy_version,
            "detector_manifest_version": self.detector_manifest_version,
            "dataset_or_scenario_id": self.dataset_or_scenario_id,
            "seed": self.seed,
            "input_length": self.input_length,
            "token_counter_type": self.token_counter_type,
            "detected_counts": self.detected_counts,
            "masked_counts": self.masked_counts,
            "check_statuses": self.check_statuses,
            "exact_round_trip": self.exact_round_trip,
            "upstream_calls": self.upstream_calls,
            "timings": self.timings,
            "degraded_components": self.degraded_components,
            "known_limitations": self.known_limitations,
            "include_synthetic": self.include_synthetic,
            "disclaimer": (
                "Этот отчёт не является криптографическим доказательством "
                "анонимности, сертификатом соответствия или формальной "
                "верификацией."
            ),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def new_run_id() -> str:
    return uuid.uuid4().hex


def build_report(
    *,
    build_version: str,
    policy_version: str,
    detector_manifest_version: str,
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
    """Build a SafeReport with a fresh run_id and timestamp."""
    return SafeReport(
        run_id=new_run_id(),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        build_version=build_version,
        policy_version=policy_version,
        detector_manifest_version=detector_manifest_version,
        dataset_or_scenario_id=dataset_or_scenario_id,
        seed=seed,
        input_length=input_length,
        detected_counts=detected_counts or {},
        masked_counts=masked_counts or {},
        check_statuses=check_statuses or {},
        exact_round_trip=exact_round_trip,
        upstream_calls=upstream_calls,
        timings=timings or {},
        degraded_components=degraded_components or [],
        known_limitations=known_limitations or [],
        include_synthetic=include_synthetic,
    )
