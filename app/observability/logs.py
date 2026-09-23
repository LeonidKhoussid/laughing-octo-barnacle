"""Safe structured logging with an allowlist of fields.

NEVER log original/masked body, vault mappings, keys, auth headers, or raw payload_id.
"""
from __future__ import annotations

import json
import logging
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


class SafeLogger:
    """Logs only allowlisted fields."""

    def __init__(self, name: str = "alfagen") -> None:
        self._logger = logging.getLogger(name)

    @staticmethod
    def new_request_id() -> str:
        return uuid.uuid4().hex

    def _emit(self, level: int, message: str, **fields: object) -> None:
        safe = {k: v for k, v in fields.items() if k in _ALLOWED_FIELDS}
        record = {"message": message, **safe}
        self._logger.log(level, json.dumps(record, ensure_ascii=False))

    def info(self, message: str, **fields: object) -> None:
        self._emit(logging.INFO, message, **fields)

    def warning(self, message: str, **fields: object) -> None:
        self._emit(logging.WARNING, message, **fields)

    def error(self, message: str, **fields: object) -> None:
        self._emit(logging.ERROR, message, **fields)
