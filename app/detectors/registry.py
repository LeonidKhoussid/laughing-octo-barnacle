"""Detector registry, loadable from config."""
from __future__ import annotations

from typing import Iterable
from pathlib import Path

import yaml

from app.detectors.address import AddressDetector
from app.detectors.base import Detector
from app.detectors.card_security import CardholderNameDetector, CvvDetector, PinDetector
from app.detectors.dates import BirthDateDetector
from app.detectors.documents import (
    DriverLicenseDetector,
    PassportIssueDateDetector,
    PassportIssuerDetector,
)
from app.detectors.identity import BirthPlaceDetector, CitizenshipDetector
from app.detectors.person import FullNameDetector
from app.detectors.regex_config import RegexDetector
from app.detectors.structured import (
    CardDetector,
    DepartmentCodeDetector,
    EmailDetector,
    InnDetector,
    PassportDetector,
    PhoneDetector,
)

_BUILTINS: dict[str, type[Detector]] = {
    "email": EmailDetector,
    "phone": PhoneDetector,
    "card": CardDetector,
    "passport": PassportDetector,
    "full_name": FullNameDetector,
    "birth_date": BirthDateDetector,
    "inn": InnDetector,
    "department_code": DepartmentCodeDetector,
    "address": AddressDetector,
    "birth_place": BirthPlaceDetector,
    "citizenship": CitizenshipDetector,
    "passport_issuer": PassportIssuerDetector,
    "passport_issue_date": PassportIssueDateDetector,
    "driver_license": DriverLicenseDetector,
    "cvv": CvvDetector,
    "pin": PinDetector,
    "cardholder_name": CardholderNameDetector,
}


class DetectorRegistry:
    """Builds enabled detectors from a detectors.yaml config."""

    def __init__(self, detectors: Iterable[Detector]) -> None:
        self._detectors = list(detectors)

    @property
    def detectors(self) -> list[Detector]:
        return self._detectors

    @classmethod
    def from_config(cls, config_path: str) -> "DetectorRegistry":
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        enabled: list[Detector] = []
        for entry in data.get("detectors", []):
            if not entry.get("enabled", True):
                continue
            det_id = entry["id"]
            if det_id == "semantic_context":
                from app.detectors.semantic import SemanticDetector
                model_dir = entry.get("model_dir", "models/semantic")
                if not Path(model_dir).is_absolute():
                    model_dir = str(Path(__file__).resolve().parents[2] / model_dir)
                enabled.append(SemanticDetector(model_dir))
                continue
            # Config-driven regex detector (C4): a new data type defined purely
            # in detectors.yaml, no core rewrite.
            if entry.get("type") == "regex":
                if not entry.get("category") or not entry.get("pattern"):
                    raise ValueError(
                        f"regex detector '{det_id}' requires 'category' and 'pattern'"
                    )
                enabled.append(
                    RegexDetector(
                        detector_id=det_id,
                        category=entry["category"],
                        pattern=entry["pattern"],
                        context=entry.get("context"),
                        context_window=entry.get("context_window", 40),
                    )
                )
                continue
            cls_type = _BUILTINS.get(det_id)
            if cls_type is None:
                raise ValueError(f"unknown detector id: {det_id}")
            enabled.append(cls_type())
        return cls(enabled)
