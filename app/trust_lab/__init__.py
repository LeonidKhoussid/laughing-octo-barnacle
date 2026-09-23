"""Trust Lab — real backend checks (master prompt section 17).

A layer on top of the shared engine, vault, policies and provider. It does NOT
rewrite detectors, the core engine, the vault or the lifecycle. It runs real
checks and reports FAILED/NOT_TESTED rather than drawing green results (R67).
"""
from __future__ import annotations

from app.trust_lab.mutations import GoldSpan, LabeledExample
from app.trust_lab.runner import TrustLabRunner

__all__ = ["GoldSpan", "LabeledExample", "TrustLabRunner"]
