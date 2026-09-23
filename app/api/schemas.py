"""Pydantic request/response models for the DEMO API.

These are PROJECT schemas, UNVERIFIED — NOT the official contract (Appendix A is
missing). The official POST /process schema is unknown.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class MaskRequest(BaseModel):
    text: str = Field(..., description="Original text to mask")
    consumer: str = Field(..., description="Consumer profile name")
    context_id: str | None = Field(
        default=None,
        description="Optional client-supplied ID for replay/retry semantics. "
        "If omitted, the server generates a fresh context_id.",
    )


class SpanInfo(BaseModel):
    """A resolved sensitive span in ORIGINAL coordinates, for UI highlighting."""

    start: int
    end: int
    category: str
    reason: str = "MASK"
    rule_id: str = ""


class MaskResponse(BaseModel):
    masked_text: str
    policy_version: str
    detected_counts: dict[str, int] = Field(default_factory=dict)
    context_id: str
    mask_action: str = "tokenize_full"
    spans: list[SpanInfo] = Field(default_factory=list)
    egress_disabled: bool = False


class UnmaskRequest(BaseModel):
    masked_text: str
    consumer: str
    context_id: str


class UnmaskResponse(BaseModel):
    original_text: str
    policy_version: str


class ChatRequest(BaseModel):
    masked_text: str
    consumer: str
    context_id: str


class ChatResponse(BaseModel):
    response: str
    provider: str
    stub: bool = True


class RestoreResponseRequest(BaseModel):
    """Restore authorized tokens inside a changed provider response."""

    response_text: str
    consumer: str
    context_id: str


class RestoreResponseResponse(BaseModel):
    restored_text: str
    policy_version: str
