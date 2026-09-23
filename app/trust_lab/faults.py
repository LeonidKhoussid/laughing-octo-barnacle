"""Controlled failure injection for a dedicated synthetic admin-only demo-run.

Fault injection is available ONLY when explicitly enabled (PII_TRUST_LAB_FAULTS
= "1") and only through the admin-protected Trust Lab endpoints. It is disabled
in the normal evaluator profile and cannot be enabled by user text (R52, section
17.2 Action C). It is never reachable via an anonymous query parameter on
/process.
"""
from __future__ import annotations

import threading


class FaultInjectionDisabled(Exception):
    """Raised when fault injection is attempted while disabled."""


class MandatoryComponentUnavailable(Exception):
    """Raised when a mandatory component (detector or Vault) is unavailable."""


class FaultInjector:
    """Tracks which mandatory components are temporarily unavailable.

    Thread-safe. Only active when ``enabled`` is True. When disabled, every
    mutation method raises FaultInjectionDisabled.
    """

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled
        self._lock = threading.Lock()
        self._down_detectors: set[str] = set()
        self._vault_down = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise FaultInjectionDisabled(
                "fault injection is disabled in this profile"
            )

    def set_detector_down(self, detector_id: str, down: bool = True) -> None:
        self._require_enabled()
        with self._lock:
            if down:
                self._down_detectors.add(detector_id)
            else:
                self._down_detectors.discard(detector_id)

    def set_vault_down(self, down: bool = True) -> None:
        self._require_enabled()
        with self._lock:
            self._vault_down = down

    def reset(self) -> None:
        self._require_enabled()
        with self._lock:
            self._down_detectors.clear()
            self._vault_down = False

    def is_detector_down(self, detector_id: str) -> bool:
        with self._lock:
            return detector_id in self._down_detectors

    @property
    def vault_down(self) -> bool:
        with self._lock:
            return self._vault_down

    def degraded_components(self) -> list[str]:
        with self._lock:
            out = [f"detector:{d}" for d in sorted(self._down_detectors)]
            if self._vault_down:
                out.append("vault")
            return out
