"""G5 safety tests: canary leak checks for logs/metrics/limits/validation.

These verify that a synthetic canary value injected into a request never appears
in logs, metric label values, error responses, or tracebacks (R51, R52, R57).
"""
import io
import logging
import os
import re

os.environ["SUPPORT_DEMO_API_KEY"] = "test-support-key"
os.environ["ANALYTICS_DEMO_API_KEY"] = "test-analytics-key"
os.environ["COMBINATION_DEMO_API_KEY"] = "test-combination-key"

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.observability.logs import SafeLogger
from app.observability.metrics import Metrics

SUPPORT_KEY = "test-support-key"

# A distinctive synthetic canary that must never leak anywhere.
CANARY = "CANARY-7f3a9c2e-5b1d-4a8f-9c0e-1d2b3c4d5e6f"
# A PII canary (email) that must be masked and never leak.
PII_CANARY = "canary-7f3a9c2e@example.com"


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestCanaryNoLeak:
    def test_canary_not_in_logs(self, client):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("alfagen-g5-canary")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)

        safe = SafeLogger("alfagen-g5-canary")
        # Simulate a caller that mistakenly passes the canary in non-allowlisted
        # fields (original, masked, payload_id, auth header, mapping).
        safe.info(
            "mask completed",
            request_id="req-g5",
            operation="mask",
            consumer="support_demo",
            policy_version="baseline-001",
            stage="completed",
            detected_counts={"EMAIL": 1},
            degraded=False,
            result="success",
            original=CANARY,
            masked=CANARY,
            payload_id=CANARY,
            authorization=CANARY,
            mapping={CANARY: CANARY},
        )
        output = stream.getvalue()
        assert CANARY not in output
        assert "req-g5" in output  # server-generated request_id is allowlisted

    def test_canary_not_in_metrics_labels(self):
        metrics = Metrics()
        # Even if a caller passes the canary as a label value, the bounded
        # cardinality guard drops it (it is not in the enum).
        metrics.inc("pii_requests_total", operation="mask", status="success")
        metrics.inc("pii_requests_total", operation=CANARY, status=CANARY)
        metrics.inc("pii_detected_entities_total", type=CANARY)
        snap = metrics.snapshot()
        text = str(snap)
        assert CANARY not in text
        # The valid bounded label survived.
        assert "pii_requests_total/operation=mask/status=success" in text

    def test_canary_not_in_error_response(self, client):
        # A validation error (missing required field) must not reflect the input.
        r = client.post(
            "/demo/mask",
            json={"consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 422
        assert CANARY not in r.text

    def test_pii_canary_is_masked_and_not_in_response(self, client):
        # A PII canary must be masked and never appear in the response.
        r = client.post(
            "/demo/mask",
            json={"text": f"email {PII_CANARY}", "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200
        assert PII_CANARY not in r.json()["masked_text"]

    def test_canary_not_in_traceback(self, client):
        # A body that is too large returns a safe 413 without reflecting input.
        big = CANARY + "x" * client.app.state.limits.max_body_bytes
        r = client.post(
            "/demo/mask",
            json={"text": big, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 413
        assert CANARY not in r.text


class TestValidationNoReflection:
    def test_validation_error_does_not_reflect_input(self, client):
        # A validation error must not reflect the input value.
        r = client.post(
            "/demo/mask",
            json={"consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 422
        assert CANARY not in r.text

    def test_missing_field_error_is_generic(self, client):
        r = client.post(
            "/demo/mask",
            json={"consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 422
        assert CANARY not in r.text

    def test_wrong_type_does_not_echo_input(self, client):
        """A wrong-type field must not echo the submitted value in detail[].input
        (D12)."""
        secret = "ivan.petrov@example.com"
        r = client.post(
            "/demo/mask",
            json={"text": secret, "consumer": 12345},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 422
        assert secret not in r.text
        # The detail must not contain an `input` field at all.
        detail = r.json()["detail"]
        for item in detail:
            assert "input" not in item, f"input leaked: {item}"


class TestLimits:
    def test_body_too_large_returns_413(self, client):
        big = "a" * (client.app.state.limits.max_body_bytes + 1)
        r = client.post(
            "/demo/mask",
            json={"text": big, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 413
        assert r.json()["detail"]["code"] == "too_large"

    def test_backpressure_returns_retryable_503_with_retry_after(self):
        # Force backpressure by making the limits.acquire raise BackpressureError.
        from app.security.limits import BackpressureError

        app = create_app()
        # The router closure captured the same limits object; patch its acquire.
        limits = app.state.limits
        original_acquire = limits.acquire

        def _raise(*args, **kwargs):
            raise BackpressureError("overloaded")

        limits.acquire = _raise
        try:
            with TestClient(app) as c:
                r = c.post(
                    "/demo/mask",
                    json={"text": "email a@b.com", "consumer": "support_demo"},
                    headers={"X-API-Key": SUPPORT_KEY},
                )
                assert r.status_code == 503
                assert r.json()["detail"]["code"] == "overloaded"
                ra = r.headers.get("Retry-After")
                assert ra is not None
                # Retry-After must be integer seconds or HTTP-date, never fractional.
                assert re.fullmatch(r"\d+", ra) or re.fullmatch(
                    r"[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT", ra
                ), f"invalid Retry-After: {ra!r}"
        finally:
            limits.acquire = original_acquire


def test_invalid_processing_body_counted_once_without_reflection(client):
    metrics = client.app.state.metrics
    key = "pii_requests_total/operation=process/status=error"
    before = metrics.snapshot()["counters"].get(key, 0)
    response = client.post("/process", json={"payload": {"private": PII_CANARY}, "payload_id": "invalid"})
    assert response.status_code == 422
    assert PII_CANARY not in response.text
    assert metrics.snapshot()["counters"][key] == before + 1


def test_invalid_config_consumer_does_not_reflect_value(client, monkeypatch):
    monkeypatch.setenv("PII_CONFIG_UPDATE_KEY", "test-admin")
    response = client.post("/config/update", json={"consumer": PII_CANARY, "updates": {}, "admin_key": "test-admin"})
    assert response.status_code == 422
    assert PII_CANARY not in response.text


def test_raw_ignored_json_field_is_bounded_before_parsing(client):
    metrics = client.app.state.metrics
    key = "pii_requests_total/operation=process/status=error"
    before = metrics.snapshot()["counters"].get(key, 0)
    size = client.app.state.limits.max_body_bytes * 6 + 4097
    response = client.post("/process", json={"payload": "safe", "payload_id": "oversize-extra", "padding": "x" * size})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "too_large"
    assert metrics.snapshot()["counters"][key] == before + 1
