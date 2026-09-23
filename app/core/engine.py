"""Engine orchestrating detection -> span resolution -> masking -> unmasking.

mask(original) returns masked text plus a token mapping. unmask(masked, mapping)
returns the exact original. The replacement is built in ONE pass from original
segments and tokens via join.

Combination rules (per-consumer policy) are applied after detection and before
span resolution. For example a PIN that requires a related CARD in the same
record is kept (not masked) when no such CARD is present, per the configured
action_if_absent. Strict profiles without combination rules always mask a signed
PIN even without a PAN.

Configurable FULL mask modes (C7 scenario 2):
- "tokenize_full" (default): typed opaque tokens ⟦PII:CATEGORY:...⟧
- "opaque_token_full": opaque unique markers [REDACTED-<n>]
Both modes are reversible via the token mapping (exact round-trip).
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Iterable

from app.core.detokenizer import detokenize
from app.core.models import Decision, Detection
from app.core.span_resolver import ResolvedSpan, resolve_spans
from app.core.tokens import TokenContext, TokenGenerator
from app.observability.telemetry import stage
from app.policies.schema import ConsumerPolicy


@dataclass
class MaskResult:
    masked_text: str
    token_mapping: dict[str, str] = field(default_factory=dict)
    resolved_spans: list[ResolvedSpan] = field(default_factory=list)
    detections: list[Detection] = field(default_factory=list)

    def __repr__(self) -> str:
        # Never expose original values (R51, D12). Show only safe metadata.
        return (
            f"MaskResult(masked_text_len={len(self.masked_text)}, "
            f"token_count={len(self.token_mapping)}, "
            f"span_count={len(self.resolved_spans)}, "
            f"detection_count={len(self.detections)})"
        )


class Engine:
    """Runs detectors and applies a deterministic edit plan."""

    def __init__(self, detectors: Iterable[object]) -> None:
        self._detectors = list(detectors)

    def mask(
        self,
        original: str,
        namespace: str,
        context_id: str,
        allowed_categories: set[str] | None = None,
        policy: ConsumerPolicy | None = None,
        mask_action: str | None = None,
    ) -> MaskResult:
        """Detect, resolve spans, and replace sensitive values with tokens.

        `mask_action` selects the FULL mask mode (C7 scenario 2):
        - "tokenize_full" (default): typed opaque tokens ⟦PII:CATEGORY:...⟧
        - "opaque_token_full": opaque unique markers [REDACTED-<n>]
        Both modes are reversible via the token mapping (exact round-trip).
        """
        detections: list[Detection] = []
        for detector in self._detectors:
            if not hasattr(detector, "refine"):
                detections.extend(detector.detect(original))
        for detector in self._detectors:
            if hasattr(detector, "refine"):
                detections = detector.refine(original, detections)

        if allowed_categories is not None:
            detections = [
                d for d in detections if d.category in allowed_categories
            ]

        if policy is not None:
            detections = self._apply_combination_rules(detections, original, policy)

        stage("detect", detected_counts=self._category_counts(detections))

        resolved = resolve_spans(detections, len(original))
        stage("resolve", detected_counts=self._category_counts(resolved))

        action = (
            mask_action
            or (policy.default_action if policy else None)
            or "tokenize_full"
        ).strip().lower()
        gen = TokenGenerator(TokenContext(namespace=namespace, context_id=context_id))
        mapping: dict[str, str] = {}
        opaque_counter = 0
        # Track markers already allocated so we never collide with the original
        # text OR with a marker already assigned to another value (C03/C09).
        allocated_markers: set[str] = set()

        # Build masked text in one pass from original segments + tokens.
        parts: list[str] = []
        cursor = 0
        for rs in resolved:
            span = rs.span
            if span.start > cursor:
                parts.append(original[cursor : span.start])
            value = original[span.start : span.end]
            if action == "opaque_token_full":
                token = self._opaque_marker(original, opaque_counter, allocated_markers)
                opaque_counter += 1
                allocated_markers.add(token)
            else:
                token = gen.token_for(rs.category, value)
                token = gen.ensure_not_in_text(token, original)
            mapping[token] = value
            parts.append(token)
            cursor = span.end
        if cursor < len(original):
            parts.append(original[cursor:])

        masked = "".join(parts)
        stage("mask", detected_counts=self._category_counts(resolved))
        return MaskResult(
            masked_text=masked,
            token_mapping=mapping,
            resolved_spans=resolved,
            detections=detections,
        )

    def unmask(self, masked: str, mapping: dict[str, str]) -> str:
        """Restore the exact original from masked text using the token mapping."""
        stage("unmask")
        return detokenize(masked, mapping)

    @staticmethod
    def _category_counts(items: Iterable[object]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            category = getattr(item, "category", None)
            if isinstance(category, str):
                counts[category] = counts.get(category, 0) + 1
        return counts

    @staticmethod
    def _opaque_marker(
        original: str, index: int, allocated: set[str] | None = None
    ) -> str:
        """Return a unique opaque marker [REDACTED-<n>].

        The marker must not appear in the original text AND must not collide
        with a marker already allocated to another value in this masking pass.
        """
        allocated = allocated or set()
        n = index
        marker = f"[REDACTED-{n}]"
        while marker in original or marker in allocated:
            n += 1
            marker = f"[REDACTED-{n}]"
        return marker

    def _apply_combination_rules(
        self,
        detections: list[Detection],
        original: str,
        policy: ConsumerPolicy,
    ) -> list[Detection]:
        """Apply per-consumer combination rules (e.g. PIN requires CARD).

        Relation scope is limited to the same record (line), not just distance.
        A card of one client and PIN of another on different lines are not linked.
        """
        if not policy.combination_rules:
            return detections

        record_of = self._record_index_for(original, detections)

        for rule in policy.combination_rules:
            if rule.action_if_absent != "keep":
                continue
            requires_by_record: dict[int, list[Detection]] = {}
            for d in detections:
                if d.category == rule.requires_type:
                    rec = record_of.get(id(d), 0)
                    requires_by_record.setdefault(rec, []).append(d)
            for d in detections:
                if d.category != rule.target_type:
                    continue
                rec = record_of.get(id(d), 0)
                if not requires_by_record.get(rec):
                    # No related requires_type in the same record -> keep unmasked.
                    d.decision = Decision.KEEP
                    d.sensitive_spans = ()
                    d.rule_id = rule.id
        return detections

    @staticmethod
    def _record_index_for(
        original: str, detections: list[Detection]
    ) -> dict[int, int]:
        """Map each detection (by id) to a record index based on line boundaries."""
        line_starts = [0]
        for i, ch in enumerate(original):
            if ch == "\n":
                line_starts.append(i + 1)

        def line_for(pos: int) -> int:
            return bisect.bisect_right(line_starts, pos) - 1

        result: dict[int, int] = {}
        for d in detections:
            if d.evidence_spans:
                result[id(d)] = line_for(d.evidence_spans[0].start)
            else:
                result[id(d)] = 0
        return result
