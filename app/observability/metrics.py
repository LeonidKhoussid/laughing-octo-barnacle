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
_TRAFFIC_WINDOW_SECONDS = 60.0
_MODEL_NAMES = frozenset({"ner", "context"})


@dataclass
class _Histogram:
    count: int = 0
    total: float = 0.0
    recent: deque[float] = field(default_factory=lambda: deque(maxlen=_HISTOGRAM_WINDOW))

# Bounded label-value enums. Any label value outside these sets is dropped so a
# stray caller cannot inflate cardinality or leak content into a label.
_ALLOWED_LABEL_VALUES: dict[str, frozenset[str]] = {
    "operation": frozenset({"mask", "unmask", "process", "chat", "restore_response"}),
    "status": frozenset({"success", "error", "overload"}),
    "type": frozenset({
        "FULL_NAME", "BIRTH_DATE", "BIRTH_PLACE", "PASSPORT", "CITIZENSHIP",
        "PASSPORT_ISSUER", "DEPARTMENT_CODE", "PASSPORT_ISSUE_DATE",
        "DRIVER_LICENSE", "ADDRESS", "EMAIL", "PHONE", "INN", "CARD",
        "CVV", "PIN", "CARDHOLDER_NAME",
    }),
    "stage": frozenset({"detect", "resolve", "mask", "unmask", "vault", "result", "errors", "completed"}),
    "reason": frozenset({
        "mask", "unmask", "process", "chat", "policy", "vault", "capacity",
        "timeout", "integrity", "context_missing", "overload",
        "admission", "context", "deadline",
    }),
    "provider": frozenset({"stub"}),
    "result": frozenset({"success", "error"}),
    "model": _MODEL_NAMES,
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
        self._lock = threading.RLock()
        self._counters: dict[tuple[str, ...], int] = defaultdict(int)
        self._histograms: dict[tuple[str, ...], _Histogram] = defaultdict(_Histogram)
        self._traffic_started_at = time.monotonic()
        self._traffic: deque[tuple[int, int, int]] = deque()
        # Exact per-inference events are retained for only the rolling window.
        # This avoids re-tokenizing and keeps model TPS independent of request TPS.
        self._model_traffic: dict[str, deque[tuple[float, int]]] = {
            model: deque() for model in _MODEL_NAMES
        }

    @staticmethod
    def _key(name: str, labels: dict[str, str]) -> tuple[str, ...]:
        return (name,) + tuple(f"{k}={v}" for k, v in _sanitize_labels(labels))

    def inc(self, name: str, **labels: str) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += 1

    def add(self, name: str, value: int, **labels: str) -> None:
        """Add a non-negative integer to a bounded-label counter."""
        if value < 0:
            raise ValueError("metric counter value must be non-negative")
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += value

    def record_request(
        self,
        *,
        operation: str,
        status: str,
        text_characters: int,
        elapsed_seconds: float,
    ) -> None:
        """Record one completed request and estimated input-token throughput.

        Tokens use ``ceil(characters / 4)``. This is intentionally an estimate:
        it does not invoke a tokenizer or rerun detection.
        """
        token_count = max(0, (max(0, text_characters) + 3) // 4)
        now = time.monotonic()
        self.inc("pii_requests_total", operation=operation, status=status)
        self.add("pii_input_tokens_estimated_total", token_count, operation=operation)
        self.observe("pii_request_duration_seconds", max(0.0, elapsed_seconds), operation=operation)
        with self._lock:
            second = int(now)
            if self._traffic and self._traffic[-1][0] == second:
                _, requests, tokens = self._traffic[-1]
                self._traffic[-1] = (second, requests + 1, tokens + token_count)
            else:
                self._traffic.append((second, 1, token_count))
            self._prune_traffic(second)

    def traffic_snapshot(self, window_seconds: float = _TRAFFIC_WINDOW_SECONDS) -> dict:
        """Return worker-local traffic rates for one explicit elapsed window."""
        if window_seconds <= 0:
            raise ValueError("traffic window must be positive")
        now = time.monotonic()
        if window_seconds > _TRAFFIC_WINDOW_SECONDS:
            raise ValueError("traffic window exceeds retained worker-local window")
        with self._lock:
            window_start = max(self._traffic_started_at, now - window_seconds)
            buckets = [bucket for bucket in self._traffic if bucket[0] >= int(window_start)]
        elapsed = now - window_start
        request_count = sum(requests for _, requests, _ in buckets)
        tokens = sum(tokens for _, _, tokens in buckets)
        return {
            "scope": "worker_local_in_process",
            "window_seconds_requested": window_seconds,
            "window_seconds_elapsed": elapsed,
            "bucket_resolution_seconds": 1,
            "request_count": request_count,
            "request_rate_per_second": request_count / elapsed if elapsed else 0.0,
            "input_tokens_estimated": tokens,
            "input_tokens_estimated_per_second": tokens / elapsed if elapsed else 0.0,
            "input_token_method": "estimated_ceil_characters_div_4",
            "token_count_unit": "estimated_input_tokens",
        }

    def record_model_tokens(self, model: str, count: int) -> None:
        """Count actual tokens accepted by a successful local model inference."""
        if model not in _MODEL_NAMES:
            return
        if count < 0:
            raise ValueError("model token count must be non-negative")
        now = time.monotonic()
        self.add("pii_model_input_tokens_total", count, model=model)
        with self._lock:
            events = self._model_traffic[model]
            events.append((now, count))
            self._prune_model_traffic(events, now)

    def model_token_snapshot(self, window_seconds: float = _TRAFFIC_WINDOW_SECONDS) -> dict:
        """Return actual model-input token rates for the bounded worker-local window."""
        if window_seconds <= 0 or window_seconds > _TRAFFIC_WINDOW_SECONDS:
            raise ValueError("model token window must be within retained worker-local window")
        now = time.monotonic()
        with self._lock:
            window_start = max(self._traffic_started_at, now - window_seconds)
            models = {
                model: sum(tokens for timestamp, tokens in events if timestamp >= window_start)
                for model, events in self._model_traffic.items()
            }
        elapsed = now - window_start
        return {
            "scope": "worker_local_in_process",
            "window_seconds_requested": window_seconds,
            "window_seconds_elapsed": elapsed,
            "token_count_unit": "actual_model_input_tokens",
            "definition": "NER includes special tokens and overlap; context includes premise and hypothesis",
            "rate_basis": "exact successful model-inference events retained for the rolling window",
            "models": {
                model: {
                    "input_tokens": count,
                    "input_tokens_per_second": count / elapsed if elapsed else 0.0,
                }
                for model, count in models.items()
            },
        }

    def _prune_traffic(self, now_second: int) -> None:
        earliest = now_second - int(_TRAFFIC_WINDOW_SECONDS) - 1
        while self._traffic and self._traffic[0][0] < earliest:
            self._traffic.popleft()

    @staticmethod
    def _prune_model_traffic(events: deque[tuple[float, int]], now: float) -> None:
        earliest = now - _TRAFFIC_WINDOW_SECONDS
        while events and events[0][0] < earliest:
            events.popleft()

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
            return {
                "counters": counters,
                "histograms": hist,
                "traffic": self.traffic_snapshot(),
                "model_token_traffic": self.model_token_snapshot(),
            }
