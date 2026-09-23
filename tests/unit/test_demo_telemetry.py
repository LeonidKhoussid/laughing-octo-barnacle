"""Demo API records one safe telemetry outcome for every handled request."""
from __future__ import annotations

import io
import logging
import re
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.demo import build_demo_router
from app.core.engine import Engine
from app.core.models import Decision, Detection, Span
from app.observability.logs import SafeLogger
from app.observability.metrics import Metrics
from app.policies.schema import ConsumerPolicy
from app.providers.stub import StubProvider
from app.security.egress import EgressGuard
from app.security.limits import Limits
from app.vault.fingerprint import Fingerprinter
from app.vault.lifecycle import Lifecycle
from app.vault.memory import MemoryVault


class _EmailDetector:
    def detect(self, text: str) -> list[Detection]:
        return [Detection(
            entity_id="email-1",
            category="EMAIL",
            evidence_spans=(Span(0, len(text)),),
            sensitive_spans=(Span(0, len(text)),),
            decision=Decision.MASK,
        )]


class _SlowEmailDetector(_EmailDetector):
    def detect(self, text: str) -> list[Detection]:
        time.sleep(0.05)
        return super().detect(text)


def _app(monkeypatch, *, detector=None, limits=None):
    monkeypatch.setenv("DEMO_TELEMETRY_KEY", "test-key")
    policy = ConsumerPolicy(
        name="safe-demo",
        namespace="safe-demo",
        authentication="api_key",
        api_key_env="DEMO_TELEMETRY_KEY",
        allow_unmask=True,
    )
    store = SimpleNamespace(
        policy_version="v1",
        get_consumer=lambda name: policy if name == "safe-demo" else None,
    )
    stream = io.StringIO()
    native = logging.getLogger("demo-telemetry")
    native.handlers = [logging.StreamHandler(stream)]
    native.setLevel(logging.INFO)
    logger = SafeLogger("demo-telemetry")
    metrics = Metrics()
    engine = Engine([detector or _EmailDetector()])
    vault = MemoryVault()
    lifecycle = Lifecycle(
        vault, engine, Fingerprinter(b"test-fp-key-32-bytes-long!!"), "v1",
    )
    app = FastAPI()
    app.include_router(build_demo_router(
        engine, vault, store, logger, metrics, limits or Limits(), EgressGuard(engine),
        StubProvider(), lifecycle,
    ))
    return app, metrics, stream


def test_mask_success_and_auth_failure_have_one_safe_request_outcome(monkeypatch):
    app, metrics, stream = _app(monkeypatch)
    original = "telemetry@example.test"
    canary = "raw-token-payload-credential@example.test"
    with TestClient(app) as client:
        ok = client.post(
            "/demo/mask", json={"text": original, "consumer": "safe-demo"},
            headers={"X-API-Key": "test-key"},
        )
        denied = client.post(
            "/demo/mask", json={"text": canary, "consumer": "unknown"},
            headers={"X-API-Key": "test-key"},
        )

    assert ok.status_code == 200
    assert denied.status_code == 403
    counters = metrics.snapshot()["counters"]
    assert counters["pii_requests_total/operation=mask/status=success"] == 1
    assert counters["pii_requests_total/operation=mask/status=error"] == 1
    output = stream.getvalue()
    assert original not in output
    assert canary not in output
    assert '"consumer": "safe-demo"' in output
    assert '"consumer": "unauthenticated"' in output
    assert '"stage": "detect"' in output
    assert '"stage": "resolve"' in output
    assert '"stage": "mask"' in output
    assert '"stage": "result"' in output
    assert '"stage": "errors"' in output
    assert len(re.findall(r'"request_id": "[0-9a-f]{32}"', output)) >= 2


def test_mask_timeout_is_demo_503_and_records_one_overload(monkeypatch):
    app, metrics, stream = _app(
        monkeypatch,
        detector=_SlowEmailDetector(),
        limits=Limits(max_processing_seconds=0.01),
    )
    with TestClient(app) as client:
        response = client.post(
            "/demo/mask",
            json={"text": "slow@example.test", "consumer": "safe-demo"},
            headers={"X-API-Key": "test-key"},
        )

    assert response.status_code == 503
    assert metrics.snapshot()["counters"]["pii_requests_total/operation=mask/status=overload"] == 1
    assert '"stage": "errors"' in stream.getvalue()
    assert '"result": "overload"' in stream.getvalue()
