"""Judge comparison uses real context decisions and the existing access limits."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.vault.memory import MemoryVault


PUBLIC = "Драматург Бернард Шоу написал «Пигмалион»."
PRIVATE = "Мой знакомый Бернард Шоу прислал личное сообщение."
NAME = "Бернард Шоу"


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(vault=MemoryVault())) as current:
        yield current


def check_context(client, text, consumer="autocheck"):
    return client.post("/trust-lab/action-a", json={
        "text": text, "consumer": consumer,
        "labeled": False, "run_variations": False,
    })


def name_decision(result, text):
    start = text.index(NAME)
    end = start + len(NAME)
    return next(decision for decision in result["decisions"]
                if decision["category"] == "FULL_NAME"
                and any(span["start"] <= start and span["end"] >= end
                        for span in decision["evidence_spans"]))


def test_same_name_context_decisions_roundtrip_and_safe_metadata(client, monkeypatch, caplog):
    engine = client.app.state.engine
    original_mask = engine.mask
    calls = []

    def tracked_mask(*args, **kwargs):
        calls.append(1)
        return original_mask(*args, **kwargs)

    monkeypatch.setattr(engine, "mask", tracked_mask)
    responses = [check_context(client, text) for text in (PUBLIC, PRIVATE)]
    assert [response.status_code for response in responses] == [200, 200]
    public, private = [response.json() for response in responses]
    assert calls == [1, 1], "explanations must reuse each existing engine run"
    assert public["masked_text"] == PUBLIC
    assert NAME not in private["masked_text"]
    kept = name_decision(public, PUBLIC)
    masked = name_decision(private, PRIVATE)
    assert kept["decision"] == "KEEP"
    assert kept["rule_id"] == "semantic-public-context"
    assert "local_nli_public_context" in kept["signals"]
    assert "historical_public_reference" not in kept["signals"]
    assert masked["decision"] == "MASK"
    private_signals = {
        "semantic-private-record": "private_record_guard",
        "semantic-private-context": "local_nli_private_context",
        "semantic-uncertain-protected": "context_uncertain_protected",
    }
    assert masked["rule_id"] in private_signals
    assert private_signals[masked["rule_id"]] in masked["signals"]
    for result, text in ((public, PUBLIC), (private, PRIVATE)):
        assert result["text"] == result["restored"] == text
        assert result["exact_round_trip"] is True
        assert result["labeled"] is False
        assert result["variations"] == []
        metadata = json.dumps(result["decisions"], ensure_ascii=False)
        assert NAME not in metadata
        assert "score" not in metadata and "confidence" not in metadata
        for decision in result["decisions"]:
            assert set(decision) == {
                "category", "decision", "rule_id", "detector_id",
                "evidence_spans", "sensitive_spans", "signals",
            }
    assert NAME not in caplog.text
    report = client.post("/trust-lab/report", json={}).json()
    assert "decisions" not in report
    assert NAME not in json.dumps(report, ensure_ascii=False)


def test_auth_body_and_admission_reject_before_engine(client, monkeypatch):
    def unexpected_mask(*args, **kwargs):
        pytest.fail("rejected request reached the engine")

    monkeypatch.setattr(client.app.state.engine, "mask", unexpected_mask)
    assert check_context(client, PRIVATE, "support_demo").status_code == 401
    limits = client.app.state.limits
    monkeypatch.setattr(limits, "max_body_bytes", 3)
    response = check_context(client, "Иван")
    assert response.status_code == 413
    assert "Иван" not in response.text
    monkeypatch.setattr(limits, "max_body_bytes", 1_048_576)
    monkeypatch.setattr(limits, "max_in_flight", 0)
    response = check_context(client, PRIVATE)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert limits._in_flight == 0


def test_failure_is_safe_and_releases_admission(client, monkeypatch):
    canary = "private-canary-must-not-leak"

    def broken_mask(*args, **kwargs):
        raise RuntimeError(canary)

    monkeypatch.setattr(client.app.state.engine, "mask", broken_mask)
    response = check_context(client, PRIVATE)
    assert response.status_code == 422
    assert canary not in response.text and PRIVATE not in response.text
    assert client.app.state.limits._in_flight == 0
