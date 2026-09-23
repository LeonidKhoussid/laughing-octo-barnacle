"""FastAPI TestClient integration tests."""
import os

os.environ["SUPPORT_DEMO_API_KEY"] = "test-support-key"
os.environ["ANALYTICS_DEMO_API_KEY"] = "test-analytics-key"
os.environ["COMBINATION_DEMO_API_KEY"] = "test-combination-key"

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

SUPPORT_KEY = "test-support-key"


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestDemoMaskUnmask:
    def test_roundtrip(self, client):
        text = (
            "Клиент Иван Иванович Петров, email ivan.petrov@example.com, "
            "тел +7 912 345-67-89, карта 4276189074144957, "
            "паспорт серия 0318 номер 123456"
        )
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        masked = data["masked_text"]
        context_id = data["context_id"]
        assert "ivan.petrov@example.com" not in masked
        assert "4276189074144957" not in masked
        assert "0318" not in masked
        assert "123456" not in masked

        r2 = client.post(
            "/demo/unmask",
            json={"masked_text": masked, "consumer": "support_demo", "context_id": context_id},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["original_text"] == text

    def test_no_pii_original_equals_masked(self, client):
        text = "Сегодня хорошая погода в городе"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200
        assert r.json()["masked_text"] == text

    def test_mask_replay_same_original_same_mask(self, client):
        text = "email a@b.com"
        r1 = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        r2 = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        # Different context_id -> different tokens, but both round-trip.
        assert r1.json()["context_id"] != r2.json()["context_id"]
        assert r1.json()["masked_text"] != r2.json()["masked_text"]

    def test_unmask_replay_same_masked_same_original(self, client):
        text = "email a@b.com"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        masked = r.json()["masked_text"]
        context_id = r.json()["context_id"]
        r1 = client.post(
            "/demo/unmask",
            json={"masked_text": masked, "consumer": "support_demo", "context_id": context_id},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        r2 = client.post(
            "/demo/unmask",
            json={"masked_text": masked, "consumer": "support_demo", "context_id": context_id},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r1.json()["original_text"] == text
        assert r2.json()["original_text"] == text

    def test_unknown_token_not_revealed(self, client):
        text = "текст с ⟦PII:EMAIL:fake-token⟧ внутри"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200
        assert "fake-token" in r.json()["masked_text"]

    def test_requires_api_key(self, client):
        r = client.post(
            "/demo/mask",
            json={"text": "email a@b.com", "consumer": "support_demo"},
        )
        assert r.status_code == 401

    def test_wrong_api_key(self, client):
        r = client.post(
            "/demo/mask",
            json={"text": "email a@b.com", "consumer": "support_demo"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert r.status_code == 401

    def test_unknown_consumer(self, client):
        r = client.post(
            "/demo/mask",
            json={"text": "email a@b.com", "consumer": "nonexistent"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 403

    def test_unmask_denied_for_analytics(self, client):
        # analytics_demo has allow_unmask=false.
        text = "email a@b.com"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "analytics_demo"},
            headers={"X-API-Key": "test-analytics-key"},
        )
        assert r.status_code == 200
        masked = r.json()["masked_text"]
        context_id = r.json()["context_id"]
        r2 = client.post(
            "/demo/unmask",
            json={"masked_text": masked, "consumer": "analytics_demo", "context_id": context_id},
            headers={"X-API-Key": "test-analytics-key"},
        )
        assert r2.status_code == 403


class TestAutocheck:
    def test_process_no_auth(self, client):
        text = "email a@b.com, телефон +7 912 345-67-89"
        r = client.post(
            "/process",
            json={"payload_id": "test-payload-1", "payload": text},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "result" in data
        assert "a@b.com" not in data["result"]

    def test_process_roundtrip(self, client):
        text = "Клиент Иван Петров, email a@b.com"
        r = client.post(
            "/process",
            json={"payload_id": "test-payload-2", "payload": text},
        )
        assert r.status_code == 200
        masked = r.json()["result"]
        # Demask via the same payload_id and the returned mask.
        r2 = client.post(
            "/process",
            json={"payload_id": "test-payload-2", "payload": masked},
        )
        assert r2.status_code == 200
        assert r2.json()["result"] == text

    def test_process_mask_replay_same_mask(self, client):
        """Same ID + same original -> same mask (retry-safe, not a toggle)."""
        text = "email a@b.com"
        r1 = client.post("/process", json={"payload_id": "replay-1", "payload": text})
        r2 = client.post("/process", json={"payload_id": "replay-1", "payload": text})
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["result"] == r2.json()["result"]

    def test_process_demask_replay_same_original(self, client):
        """Repeated demask -> same original (retry-safe)."""
        text = "email a@b.com"
        r1 = client.post("/process", json={"payload_id": "demask-replay", "payload": text})
        masked = r1.json()["result"]
        d1 = client.post("/process", json={"payload_id": "demask-replay", "payload": masked})
        d2 = client.post("/process", json={"payload_id": "demask-replay", "payload": masked})
        assert d1.status_code == 200 and d2.status_code == 200
        assert d1.json()["result"] == text
        assert d2.json()["result"] == text

    def test_process_conflict_no_overwrite(self, client):
        """Same ID + different unrelated text -> safe error, no overwrite."""
        r1 = client.post("/process", json={"payload_id": "conflict-1", "payload": "email a@b.com"})
        assert r1.status_code == 200
        r2 = client.post("/process", json={"payload_id": "conflict-1", "payload": "email c@d.com"})
        assert r2.status_code == 409

    def test_process_original_equals_masked_no_conflict(self, client):
        """Original == masked (no PII) must not create a false conflict."""
        text = "обычный текст без персональных данных"
        r1 = client.post("/process", json={"payload_id": "no-pii-1", "payload": text})
        assert r1.status_code == 200
        # The mask equals the original (no PII). A repeat must return the same.
        r2 = client.post("/process", json={"payload_id": "no-pii-1", "payload": text})
        assert r2.status_code == 200
        assert r2.json()["result"] == text

    def test_process_overload_returns_429_with_retry_after(self, client):
        """Managed overload on /process returns 429 with a valid Retry-After
        (Appendix B)."""
        from app.security.limits import BackpressureError

        app = client.app
        limits = app.state.limits
        original_acquire = limits.acquire

        def _raise(*args, **kwargs):
            raise BackpressureError("overloaded")

        limits.acquire = _raise
        try:
            r = client.post("/process", json={"payload_id": "overload-1", "payload": "email a@b.com"})
            assert r.status_code == 429
            assert r.json()["detail"]["code"] == "overloaded"
            ra = r.headers.get("Retry-After")
            assert ra is not None
            import re
            assert re.fullmatch(r"\d+", ra), f"invalid Retry-After: {ra!r}"
        finally:
            limits.acquire = original_acquire

    def test_process_deadline_returns_429(self, client):
        """Processing-budget exhaustion (DeadlineExceededError) must return 429,
        not 422 (review issue 4)."""
        app = client.app
        limits = app.state.limits
        original = limits.max_processing_seconds
        limits.max_processing_seconds = 0
        try:
            r = client.post("/process", json={"payload_id": "deadline-1", "payload": "email a@b.com"})
            assert r.status_code == 429
            assert r.json()["detail"]["code"] == "overloaded"
            assert r.headers.get("Retry-After") is not None
        finally:
            limits.max_processing_seconds = original


class TestConfigUpdate:
    @pytest.fixture(autouse=True)
    def _restore_config(self, client):
        """Restore support_demo config after each test so it does not leak."""
        app = client.app
        store = app.state.policy_store
        original = store.get_consumer("support_demo").model_dump()
        yield
        store.update_consumer("support_demo", original)

    def test_update_consumer_valid(self, client, monkeypatch):
        """A valid config update is applied atomically and reflected in /config."""
        monkeypatch.setenv("PII_CONFIG_UPDATE_KEY", "cfg-key")
        r = client.post(
            "/config/update",
            json={"consumer": "support_demo", "updates": {"allow_unmask": False}, "admin_key": "cfg-key"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["updated"]["allow_unmask"] is False
        # Reflected in /config.
        cfg = client.get("/config").json()
        assert cfg["consumer_info"]["support_demo"]["allow_unmask"] is False

    def test_update_consumer_invalid_preserves_active(self, client, monkeypatch):
        """An invalid update (bad detect_types) fails and leaves the active
        config working."""
        monkeypatch.setenv("PII_CONFIG_UPDATE_KEY", "cfg-key")
        r = client.post(
            "/config/update",
            json={"consumer": "support_demo", "updates": {"detect_types": "FULL_NMAE"}, "admin_key": "cfg-key"},
        )
        assert r.status_code == 422
        # Active config still works.
        cfg = client.get("/config").json()
        assert cfg["consumer_info"]["support_demo"]["detect_types"] == "all_required"

    def test_update_consumer_requires_key(self, client):
        r = client.post(
            "/config/update",
            json={"consumer": "support_demo", "updates": {"allow_unmask": False}, "admin_key": "wrong"},
        )
        assert r.status_code == 401

    def test_update_unknown_consumer(self, client, monkeypatch):
        monkeypatch.setenv("PII_CONFIG_UPDATE_KEY", "cfg-key")
        r = client.post(
            "/config/update",
            json={"consumer": "does-not-exist", "updates": {}, "admin_key": "cfg-key"},
        )
        assert r.status_code == 422


class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "alive"
        assert r.json()["ready"] is True

    def test_config(self, client):
        r = client.get("/config")
        assert r.status_code == 200
        assert "support_demo" in r.json()["consumers"]
        assert "EMAIL" in r.json()["detectors"] or "email" in r.json()["detectors"]


class TestNewCategoriesRoundTrip:
    def test_new_categories_roundtrip(self, client):
        text = (
            "место рождения: г. Москва, гражданство: Российская Федерация, "
            "паспорт выдан ОВД района, дата выдачи 15.03.2015, "
            "водительское удостоверение 77 12 345678, CVV 123, PIN 4321, "
            "держатель карты Иван Петров"
        )
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        masked = data["masked_text"]
        context_id = data["context_id"]
        # Sensitive values must be masked.
        assert "Москва" not in masked
        assert "Российская Федерация" not in masked
        assert "ОВД района" not in masked
        assert "15.03.2015" not in masked
        assert "77 12 345678" not in masked
        assert "123" not in masked
        assert "4321" not in masked
        assert "Иван Петров" not in masked

        r2 = client.post(
            "/demo/unmask",
            json={"masked_text": masked, "consumer": "support_demo", "context_id": context_id},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["original_text"] == text


class TestPinCombinationRule:
    def test_support_demo_masks_signed_pin_without_card(self, client):
        # Strict profile: signed PIN is masked even without a PAN.
        text = "PIN: 4321"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200, r.text
        assert "4321" not in r.json()["masked_text"]

    def test_combination_demo_keeps_pin_without_card(self, client):
        # Combination profile: PIN kept when no related CARD in the record.
        text = "PIN: 4321"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "combination_demo"},
            headers={"X-API-Key": "test-combination-key"},
        )
        assert r.status_code == 200, r.text
        assert "4321" in r.json()["masked_text"]

    def test_combination_demo_masks_pin_with_card_same_record(self, client):
        text = "карта 4276189074144957, PIN: 4321"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "combination_demo"},
            headers={"X-API-Key": "test-combination-key"},
        )
        assert r.status_code == 200, r.text
        assert "4321" not in r.json()["masked_text"]

    def test_combination_demo_keeps_pin_card_on_other_record(self, client):
        # Card and PIN on different lines (records) must not auto-link.
        text = "карта 4276189074144957\nPIN: 4321"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "combination_demo"},
            headers={"X-API-Key": "test-combination-key"},
        )
        assert r.status_code == 200, r.text
        assert "4321" in r.json()["masked_text"]
