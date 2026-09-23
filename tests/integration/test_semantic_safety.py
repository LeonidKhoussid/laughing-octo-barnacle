"""Mandatory semantic inference fails closed through both HTTP interfaces."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.detectors.semantic import SemanticDetector
from app.main import create_app
from app.nlp.model_runtime import ModelUnavailable
from app.providers.stub import StubProvider


CANARY = "semantic-fault-canary@example.com"
TEXT = ("Поэт Александр Сергеевич Пушкин написал роман «Евгений Онегин».\n"
        f"Email клиента: {CANARY}.")


@pytest.fixture(scope="module")
def application():
    return create_app()


def semantic_runtime(application):
    return next(detector.runtime for detector in application.state.engine._detectors
                if isinstance(detector, SemanticDetector))


def assert_safe_retry(response):
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json() == {"detail": {"code": "retryable", "message": "temporarily unavailable; retry later"}}
    assert CANARY not in response.text
    assert "result" not in response.json() and "masked_text" not in response.json()


@pytest.mark.parametrize("endpoint", ["/process", "/demo/mask"])
@pytest.mark.parametrize("stage", ["ner", "nli"])
def test_inference_failure_is_safe_retry_and_same_id_recovers(application, monkeypatch, caplog, endpoint, stage):
    runtime = semantic_runtime(application)
    context = uuid.uuid4().hex
    payload = ({"payload": TEXT, "payload_id": context} if endpoint == "/process"
               else {"text": TEXT, "consumer": "autocheck", "context_id": context})
    called = []
    def fail(value):
        assert value  # The NLI fault must reach a real semantic job.
        called.append(True)
        raise ModelUnavailable()
    with TestClient(application) as client:
        with monkeypatch.context() as fault:
            fault.setattr(runtime, stage, fail)
            assert_safe_retry(client.post(endpoint, json=payload))
        assert called
        recovered = client.post(endpoint, json=payload)
        assert recovered.status_code == 200, recovered.text
        field = "result" if endpoint == "/process" else "masked_text"
        masked = recovered.json()[field]
        assert CANARY not in masked
        if endpoint == "/process":
            restored = client.post(endpoint, json={"payload": masked, "payload_id": context})
            assert restored.json() == {"result": TEXT}
        else:
            restored = client.post("/demo/unmask", json={"masked_text": masked, "consumer": "autocheck", "context_id": context})
            assert restored.json()["original_text"] == TEXT
        assert restored.status_code == 200
    assert CANARY not in caplog.text


def test_missing_required_model_prevents_startup_without_regex_fallback(tmp_path):
    config = tmp_path / "detectors.yaml"
    config.write_text("detectors:\n  - id: semantic_context\n    enabled: true\n    model_dir: " + str(tmp_path / "missing") + "\n")
    with pytest.raises(ModelUnavailable) as failure:
        create_app(detectors_path=str(config))
    assert str(failure.value) == "Local semantic model is unavailable."
    assert str(tmp_path) not in str(failure.value)


def test_semantic_failure_blocks_provider_and_recovery_has_positive_control(application, monkeypatch):
    monkeypatch.setenv("DEMO_CHAT_API_KEY", "semantic-test-key")
    headers = {"X-API-Key": "semantic-test-key"}
    calls = []
    original = StubProvider.complete
    def spy(self, text):
        calls.append(text)
        return original(self, text)
    monkeypatch.setattr(StubProvider, "complete", spy)
    runtime = semantic_runtime(application)
    with TestClient(application) as client:
        masked = client.post("/demo/mask", json={"text": f"Email клиента: {CANARY}", "consumer": "demo_chat"}, headers=headers)
        assert masked.status_code == 200, masked.text
        data = masked.json()
        request = {"consumer": "demo_chat", "context_id": data["context_id"], "masked_text": data["masked_text"]}
        def fail(text):
            raise ModelUnavailable()
        with monkeypatch.context() as fault:
            fault.setattr(runtime, "ner", fail)
            assert_safe_retry(client.post("/demo/chat", json=request, headers=headers))
        assert calls == []
        healthy = client.post("/demo/chat", json=request, headers=headers)
        assert healthy.status_code == 200, healthy.text
        assert len(calls) == 1
        assert CANARY not in calls[0]
