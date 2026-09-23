"""Body size limits, in-flight backpressure, and a per-request processing deadline."""
from __future__ import annotations

import threading
import time


class BodyTooLargeError(Exception):
    """Raised when the request body exceeds the configured limit."""


class BackpressureError(Exception):
    """Raised when too many requests are in flight."""


class DeadlineExceededError(Exception):
    """Raised when a request exceeds the configured processing deadline."""


class Limits:
    """Configurable body size, in-flight, and processing-deadline limits."""

    def __init__(
        self,
        max_body_bytes: int = 1_048_576,
        max_in_flight: int = 64,
        max_processing_seconds: float = 30.0,
    ) -> None:
        self.max_body_bytes = max_body_bytes
        self.max_in_flight = max_in_flight
        self.max_processing_seconds = max_processing_seconds
        self._lock = threading.Lock()
        self._in_flight = 0

    def check_body(self, size_bytes: int) -> None:
        if size_bytes > self.max_body_bytes:
            raise BodyTooLargeError("request body too large")

    def acquire(self) -> None:
        with self._lock:
            if self._in_flight >= self.max_in_flight:
                raise BackpressureError("too many requests in flight")
            self._in_flight += 1

    def release(self) -> None:
        with self._lock:
            self._in_flight -= 1

    def check_deadline(self, started_at: float) -> None:
        """Raise DeadlineExceededError if the request has run past the deadline.

        ``started_at`` is a ``time.monotonic()`` timestamp captured when the
        request began processing. Callers map DeadlineExceededError to a
        retryable overload response.
        """
        if time.monotonic() - started_at > self.max_processing_seconds:
            raise DeadlineExceededError("request exceeded processing deadline")
