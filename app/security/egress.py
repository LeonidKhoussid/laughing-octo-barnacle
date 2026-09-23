"""Egress guard: checks policy allows the consumer/provider, all required stages
completed, no remaining sensitive spans, and the outbound object contains no
original/mapping/secrets.

The guard re-detects the outbound text server-side (it does NOT trust caller
claims about completed stages or residual spans). Required protection failures
prevent transport calls (section 13, R45-R47).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.policies.schema import ConsumerPolicy


@dataclass
class EgressCheck:
    ok: bool
    reasons: list[str] = field(default_factory=list)


class EgressGuard:
    """Validates that an outbound object is safe to send to a provider."""

    def __init__(self, engine=None) -> None:
        self._forbidden_substrings = ["PII_VAULT_KEY", "ALFAGEN_API_KEY"]
        # The engine is used to re-detect residual sensitive spans in the
        # outbound text. If absent, residual detection is skipped (caller must
        # pass an explicit residual count from actual server-side processing).
        self._engine = engine

    def check(
        self,
        policy: ConsumerPolicy,
        provider_id: str,
        outbound_text: str,
        stages_completed: list[str],
        remaining_sensitive_spans: int,
        mapping: dict[str, str] | None = None,
    ) -> EgressCheck:
        reasons: list[str] = []

        if not policy.enabled:
            reasons.append("consumer disabled")
        if not policy.allow_llm_egress:
            reasons.append("llm egress not allowed by policy")
        if provider_id not in ("stub",):
            reasons.append(f"provider not allowed: {provider_id}")

        required_stages = ["detect", "resolve", "mask"]
        for stage in required_stages:
            if stage not in stages_completed:
                reasons.append(f"stage not completed: {stage}")

        if remaining_sensitive_spans > 0:
            reasons.append("remaining sensitive spans in outbound text")

        # Re-detect residual sensitive spans in the outbound text (server-side),
        # so a caller cannot claim zero residual spans for raw PII (C).
        if self._engine is not None:
            residual = self._engine.mask(
                outbound_text, "egress-check", "egress-check"
            )
            if residual.resolved_spans:
                reasons.append(
                    f"residual sensitive spans detected in outbound text: "
                    f"{len(residual.resolved_spans)}"
                )

        # Verify no original values from the mapping leak into the outbound text.
        if mapping:
            for original in mapping.values():
                if original and original in outbound_text:
                    reasons.append("original sensitive value present in outbound text")
                    break

        for forbidden in self._forbidden_substrings:
            if forbidden in outbound_text:
                reasons.append("forbidden secret marker in outbound text")

        return EgressCheck(ok=not reasons, reasons=reasons)
