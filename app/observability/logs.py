"""Safe structured logging with an allowlist of fields.

NEVER log original/masked body, vault mappings, keys, auth headers, or raw payload_id.
"""
from __future__ import annotations

import json
import logging
import sys
import uuid

_ALLOWED_FIELDS = {
    "request_id",
    "operation",
    "consumer",
    "policy_version",
    "stage",
    "detected_counts",
    "degraded",
    "result",
}

_ALLOWED_MESSAGES = {
    "mask completed",
    "unmask completed",
    "process completed",
    "chat completed",
    "restore-response completed",
    "processing stage",
}
_CATEGORIES = frozenset({
    "FULL_NAME", "BIRTH_DATE", "BIRTH_PLACE", "PASSPORT", "CITIZENSHIP",
    "PASSPORT_ISSUER", "DEPARTMENT_CODE", "PASSPORT_ISSUE_DATE",
    "DRIVER_LICENSE", "ADDRESS", "EMAIL", "PHONE", "INN", "CARD",
    "CVV", "PIN", "CARDHOLDER_NAME",
})
_OPERATIONS = frozenset({"mask", "unmask", "process", "chat", "restore_response"})
_STAGES = frozenset({"detect", "resolve", "mask", "unmask", "vault", "result", "errors", "completed"})
_RESULTS = frozenset({"success", "error", "overload"})


class SafeLogger:
    """Logs only allowlisted fields."""

    def __init__(self, name: str = "alfagen") -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(logging.INFO)
        # Uvicorn configures its own loggers, not the application logger.
        # Emit structured events even when the root logger has no handler.
        if not self._logger.hasHandlers():
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)

    @staticmethod
    def new_request_id() -> str:
        return uuid.uuid4().hex

    def _emit(self, level: int, message: str, **fields: object) -> None:
        safe = {k: v for k, v in fields.items() if k in _ALLOWED_FIELDS}
        for name, allowed in (("operation", _OPERATIONS), ("stage", _STAGES), ("result", _RESULTS)):
            if safe.get(name) not in allowed:
                safe.pop(name, None)
        counts = safe.get("detected_counts")
        if isinstance(counts, dict):
            safe["detected_counts"] = {
                category: count
                for category, count in counts.items()
                if category in _CATEGORIES and isinstance(count, int) and count >= 0
            }
        elif counts is not None:
            safe.pop("detected_counts", None)
        record = {"message": message if message in _ALLOWED_MESSAGES else "processing event", **safe}
        self._logger.log(level, json.dumps(record, ensure_ascii=False))

    def info(self, message: str, **fields: object) -> None:
        self._emit(logging.INFO, message, **fields)

    def warning(self, message: str, **fields: object) -> None:
        self._emit(logging.WARNING, message, **fields)

    def error(self, message: str, **fields: object) -> None:
        self._emit(logging.ERROR, message, **fields)
