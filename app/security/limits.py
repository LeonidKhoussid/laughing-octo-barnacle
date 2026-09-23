"""Body size limits, in-flight backpressure, and a per-request processing deadline."""
from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

_T = TypeVar("_T")


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
        max_body_bytes: int = 2_097_152,
        max_in_flight: int = 8,
        max_processing_seconds: float = 9.0,
    ) -> None:
        if max_body_bytes <= 0 or max_in_flight <= 0:
            raise ValueError("request limits must be positive")
        if not math.isfinite(max_processing_seconds) or max_processing_seconds <= 0:
            raise ValueError("processing deadline must be finite and positive")
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
            self._in_flight = max(0, self._in_flight - 1)

    async def run(
        self, callback: Callable[[], _T], *, run_sync: Callable[..., Awaitable[_T]],
    ) -> _T:
        """Bound queueing and response time without abandoning admission safety.

        Native inference cannot be interrupted safely. On timeout the response
        returns immediately, but a running worker retains its slot until it
        finishes (including committing a replayable mapping). A queued worker
        is marked abandoned and cannot start processing after the timeout.
        """
        self.acquire()
        lock = threading.Lock()
        execution_started = False
        abandoned = False
        started_at = time.monotonic()

        def execute():
            nonlocal execution_started
            with lock:
                if abandoned:
                    return None
                execution_started = True
            try:
                return callback()
            finally:
                self.release()

        deadline = asyncio.timeout(self.max_processing_seconds)
        try:
            async with deadline:
                result = await run_sync(execute)
            self.check_deadline(started_at)
            return result
        except TimeoutError:
            if deadline.expired():
                raise DeadlineExceededError("request exceeded processing deadline") from None
            raise
        finally:
            with lock:
                if not execution_started:
                    abandoned = True
                    self.release()

    def check_deadline(self, started_at: float) -> None:
        """Raise DeadlineExceededError if the request has run past the deadline.

        ``started_at`` is a ``time.monotonic()`` timestamp captured when the
        request began processing. Callers map DeadlineExceededError to a
        retryable overload response.
        """
        if time.monotonic() - started_at > self.max_processing_seconds:
            raise DeadlineExceededError("request exceeded processing deadline")
