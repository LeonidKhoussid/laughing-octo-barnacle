"""Unit tests for Limits: UTF-8-aware body size, in-flight backpressure, deadline."""
import threading
import time

import pytest

from app.security.limits import BackpressureError, BodyTooLargeError, DeadlineExceededError, Limits


class TestBodyLimit:
    def test_utf8_aware_body_size(self):
        # max_body_bytes is in bytes; a multi-byte UTF-8 char counts as its bytes.
        limits = Limits(max_body_bytes=10)
        # "é" is 2 bytes in UTF-8.
        limits.check_body(len("ééééé".encode("utf-8")))  # 10 bytes, ok
        with pytest.raises(BodyTooLargeError):
            limits.check_body(len("éééééé".encode("utf-8")))  # 12 bytes, too large

    def test_ascii_body_size(self):
        limits = Limits(max_body_bytes=5)
        limits.check_body(len("hello".encode("utf-8")))
        with pytest.raises(BodyTooLargeError):
            limits.check_body(len("hello!".encode("utf-8")))


class TestBackpressure:
    def test_acquire_release(self):
        limits = Limits(max_in_flight=2)
        limits.acquire()
        limits.acquire()
        with pytest.raises(BackpressureError):
            limits.acquire()
        limits.release()
        limits.acquire()  # now free again

    def test_release_does_not_go_negative(self):
        limits = Limits(max_in_flight=1)
        limits.acquire()
        limits.release()
        limits.release()  # should not go negative / raise
        limits.acquire()
        with pytest.raises(BackpressureError):
            limits.acquire()  # duplicate release cannot create capacity

    def test_concurrent_backpressure(self):
        limits = Limits(max_in_flight=4)
        errors = []
        lock = threading.Lock()

        def worker():
            try:
                limits.acquire()
                time.sleep(0.01)
                limits.release()
            except BackpressureError:
                with lock:
                    errors.append(1)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Some requests must have been rejected under concurrency.
        assert len(errors) > 0


class TestDeadline:
    def test_deadline_not_exceeded(self):
        limits = Limits(max_processing_seconds=30.0)
        started = time.monotonic()
        limits.check_deadline(started)  # should not raise

    def test_deadline_exceeded(self):
        limits = Limits(max_processing_seconds=0.001)
        started = time.monotonic() - 1.0  # pretend we started 1s ago
        with pytest.raises(DeadlineExceededError):
            limits.check_deadline(started)

    def test_deadline_default_does_not_raise_immediately(self):
        limits = Limits()
        limits.check_deadline(time.monotonic())
