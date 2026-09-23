"""Bounded-cardinality counters/histograms using simple in-process counters.

Cardinality is bounded because label values are drawn from small enums
(operation, status, type, stage, reason), never from request content. No PII,
token, payload_id, or request_id ever appears in a label value (R51, section 15.3).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

_HISTOGRAM_WINDOW = 8192


@dataclass
class _Histogram:
    count: int = 0
    total: float = 0.0
    recent: deque[float] = field(default_factory=lambda: deque(maxlen=_HISTOGRAM_WINDOW))

# Bounded label-value enums. Any label value outside these sets is dropped so a
# stray caller cannot inflate cardinality or leak content into a label.
_ALLOWED_LABEL_VALUES: dict[str, frozenset[str]] = {
    "operation": frozenset({"mask", "unmask", "process", "chat"}),
    "status": frozenset({"success", "error", "overload"}),
    "type": frozenset({
        "FULL_NAME", "BIRTH_DATE", "BIRTH_PLACE", "PASSPORT", "CITIZENSHIP",
        "PASSPORT_ISSUER", "DEPARTMENT_CODE", "PASSPORT_ISSUE_DATE",
        "DRIVER_LICENSE", "ADDRESS", "EMAIL", "PHONE", "INN", "CARD",
        "CVV", "PIN", "CARDHOLDER_NAME",
    }),
    "stage": frozenset({"detect", "resolve", "mask", "unmask", "vault", "completed"}),
    "reason": frozenset({
        "mask", "unmask", "process", "chat", "policy", "vault", "capacity",
        "timeout", "integrity", "context_missing", "overload",
    }),
    "provider": frozenset({"stub"}),
    "result": frozenset({"success", "error"}),
}


def _sanitize_labels(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """Keep only labels whose value is in the bounded enum for that key."""
    out: list[tuple[str, str]] = []
    for k, v in labels.items():
        allowed = _ALLOWED_LABEL_VALUES.get(k)
        if allowed is not None and v in allowed:
            out.append((k, v))
    return tuple(sorted(out))


class Metrics:
    """In-process metrics with bounded cardinality."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, ...], int] = defaultdict(int)
        self._histograms: dict[tuple[str, ...], _Histogram] = defaultdict(_Histogram)

    @staticmethod
    def _key(name: str, labels: dict[str, str]) -> tuple[str, ...]:
        return (name,) + tuple(f"{k}={v}" for k, v in _sanitize_labels(labels))

    def inc(self, name: str, **labels: str) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += 1

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = self._key(name, labels)
        with self._lock:
            histogram = self._histograms[key]
            histogram.count += 1
            histogram.total += value
            histogram.recent.append(value)

    def timed(self, name: str, **labels: str):
        """Context manager recording duration in seconds."""

        class _Ctx:
            def __init__(self, metrics: "Metrics", name: str, labels: dict[str, str]):
                self._m = metrics
                self._name = name
                self._labels = labels
                self._start = time.monotonic()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._m.observe(self._name, time.monotonic() - self._start, **self._labels)
                return False

        return _Ctx(self, name, labels)

    @staticmethod
    def _percentile(sorted_vals: list[float], q: float) -> float:
        if not sorted_vals:
            return 0.0
        idx = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
        return sorted_vals[idx]

    def snapshot(self) -> dict:
        with self._lock:
            counters = {"/".join(k): v for k, v in self._counters.items()}
            hist = {}
            for k, v in self._histograms.items():
                if v.count:
                    sv = sorted(v.recent)
                    hist["/".join(k)] = {
                        "count": v.count,
                        "mean": v.total / v.count,
                        "percentile_scope": "most_recent_observations",
                        "window_count": len(sv),
                        "p50": self._percentile(sv, 0.50),
                        "p95": self._percentile(sv, 0.95),
                        "p99": self._percentile(sv, 0.99),
                    }
            return {"counters": counters, "histograms": hist}
