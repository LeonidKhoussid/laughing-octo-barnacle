"""Pydantic models for consumer policy config."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ALL_REQUIRED_CATEGORIES = frozenset(
    {
        "FULL_NAME",
        "BIRTH_DATE",
        "BIRTH_PLACE",
        "PASSPORT",
        "CITIZENSHIP",
        "PASSPORT_ISSUER",
        "DEPARTMENT_CODE",
        "PASSPORT_ISSUE_DATE",
        "DRIVER_LICENSE",
        "ADDRESS",
        "EMAIL",
        "PHONE",
        "INN",
        "CARD",
        "CVV",
        "PIN",
        "CARDHOLDER_NAME",
    }
)


class CombinationRule(BaseModel):
    id: str
    target_type: str
    requires_type: str
    relation_scope: str
    action_if_present: str
    action_if_absent: str


class ConsumerPolicy(BaseModel):
    name: str | None = None
    enabled: bool = True
    namespace: str
    authentication: Literal["competition_exception", "api_key", "none"] = "api_key"
    api_key_env: str | None = None
    detect_types: str = "all_required"
    default_action: str = "tokenize_full"
    allow_unmask: bool = False
    allow_llm_egress: bool = False
    synthetic_only: bool = False
    combination_rules: list[CombinationRule] = Field(default_factory=list)

    def detect_types_set(self) -> set[str]:
        """Resolve detect_types to a set of category names.

        "all_required" -> all 17 categories. Otherwise a comma-separated list of
        category names (whitespace trimmed, case-insensitive). Unknown category
        names are rejected (D04/D07): a typo like FULL_NMAE must not silently
        disable detection.
        """
        raw = (self.detect_types or "all_required").strip()
        if not raw or raw.lower() == "all_required":
            return set(ALL_REQUIRED_CATEGORIES)
        parts = {part.strip().upper() for part in raw.split(",") if part.strip()}
        unknown = parts - set(ALL_REQUIRED_CATEGORIES)
        if unknown:
            raise ValueError(
                f"unknown detect_types category(s): {', '.join(sorted(unknown))}"
            )
        return parts


class PolicyConfig(BaseModel):
    schema_version: int = 1
    policy_version: str = "baseline-001"
    consumers: dict[str, ConsumerPolicy]
