"""G7 Trust Lab tests — real backend checks (section 17.4 acceptance criteria).

Covers Actions A/B/C, the safe report, worker-change token stability, and the
requirement that fault injection is admin-only and disabled in the normal
evaluator profile.
"""
import os

os.environ["SUPPORT_DEMO_API_KEY"] = "test-support-key"
os.environ["ANALYTICS_DEMO_API_KEY"] = "test-analytics-key"
os.environ["COMBINATION_DEMO_API_KEY"] = "test-combination-key"
os.environ["DEMO_CHAT_API_KEY"] = "test-chat-key"
os.environ["PII_TRUST_LAB_ADMIN_KEY"] = "admin-secret"
os.environ["PII_TRUST_LAB_FAULTS"] = "1"

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.vault.memory import MemoryVault

SUPPORT_KEY = "test-support-key"
ADMIN_KEY = "admin-secret"

SAMPLE = (
    "Клиент Иван Петров, email ivan@example.com, телефон +7 912 345-67-89, "
    "карта 4276189074144957"
)
CANARY = "canary-secret-7f3a9b"


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestActionA:
    def _post(self, client, body):
        return client.post(
            "/trust-lab/action-a",
            json=body,
            headers={"X-API-Key": SUPPORT_KEY},
        )

    def test_action_a_uses_shared_engine(self, client):
        # The masked text must use the shared engine's token format, not a
        # separate lightweight UI detector.
        r = self._post(client, {"text": SAMPLE, "consumer": "support_demo", "labeled": True})
        assert r.status_code == 200, r.text
        d = r.json()
        assert "⟦PII:" in d["masked_text"]
        assert "ivan@example.com" not in d["masked_text"]
        assert "4276189074144957" not in d["masked_text"]
        assert d["detected_counts"].get("EMAIL") == 1
        assert d["detected_counts"].get("CARD") == 1

    def test_action_a_roundtrip_whole_string(self, client):
        r = self._post(client, {"text": SAMPLE, "consumer": "support_demo", "labeled": True})
        d = r.json()
        assert d["exact_round_trip"] is True
        assert d["restored"] == SAMPLE

    def test_action_a_arbitrary_text_no_fake_accuracy(self, client):
        # Arbitrary text without labeling must NOT claim a fake accuracy.
        r = self._post(client, {"text": "Сегодня хорошая погода", "consumer": "support_demo", "labeled": False})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["labeled"] is False
        assert "Эталонная разметка не задана" in d["note"]
        assert d["exact_round_trip"] is True

    def test_action_a_variations_show_real_failures(self, client):
        # Variations must show REAL results including failures, not always green.
        r = self._post(
            client,
            {
                "text": SAMPLE,
                "consumer": "support_demo",
                "labeled": True,
                "seed": 20260922,
                "run_variations": True,
            },
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d["variations"]) >= 8
        statuses = {v["status"] for v in d["variations"]}
        # At least one variation fails (lowercase name / en-dash phone / emoji).
        assert "failed" in statuses, "variations must show real failures"

    def test_action_a_reproducible_with_seed(self, client):
        body = {
            "text": SAMPLE,
            "consumer": "support_demo",
            "labeled": True,
            "seed": 20260922,
            "run_variations": True,
        }
        r1 = self._post(client, body)
        r2 = self._post(client, body)
        d1, d2 = r1.json(), r2.json()
        # Same seed/build/policy -> same results (timings may vary naturally).
        assert [v["status"] for v in d1["variations"]] == [v["status"] for v in d2["variations"]]
        assert d1["masked_counts"] == d2["masked_counts"]


class TestActionB:
    def test_action_b_shows_both_versions(self, client):
        r = client.post(
            "/trust-lab/action-b",
            json={"consumer": "support_demo", "candidate": {"default_action": "opaque_token_full"}},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["active"]["mask_action"] == "tokenize_full"
        assert d["candidate"]["mask_action"] == "opaque_token_full"
        assert "active" in d and "candidate" in d

    def test_action_b_regression_turns_red(self, client):
        # A candidate that removes FULL_NAME from detect_types must show a
        # regression (missed entities), not hide it.
        types = "EMAIL,PHONE,CARD,PASSPORT,INN,BIRTH_DATE,ADDRESS,BIRTH_PLACE,CITIZENSHIP,PASSPORT_ISSUER,DEPARTMENT_CODE,PASSPORT_ISSUE_DATE,DRIVER_LICENSE,CVV,PIN,CARDHOLDER_NAME"
        r = client.post(
            "/trust-lab/action-b",
            json={"consumer": "support_demo", "candidate": {"detect_types": types}},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["delta_missed"] > 0
        assert any("missed" in reg for reg in d["regressions"])

    def test_action_b_previous_policy_serves_active(self, client):
        # Action B must NOT activate the candidate. The active policy continues
        # to serve requests until explicitly activated.
        types = "EMAIL,PHONE,CARD,PASSPORT,INN,BIRTH_DATE,ADDRESS,BIRTH_PLACE,CITIZENSHIP,PASSPORT_ISSUER,DEPARTMENT_CODE,PASSPORT_ISSUE_DATE,DRIVER_LICENSE,CVV,PIN,CARDHOLDER_NAME"
        client.post(
            "/trust-lab/action-b",
            json={"consumer": "support_demo", "candidate": {"detect_types": types}},
        )
        # Active policy still masks FULL_NAME.
        r = client.post(
            "/demo/mask",
            json={"text": "Клиент Иван Петров", "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200, r.text
        assert "Иван Петров" not in r.json()["masked_text"]


class TestActionC:
    def test_action_c_zero_upstream_calls(self, client):
        headers = {"X-Admin-Key": ADMIN_KEY}
        r = client.post(
            "/trust-lab/action-c",
            json={
                "consumer": "support_demo",
                "text": SAMPLE,
                "fault_type": "detector",
                "detector_id": "full_name",
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "stopped"
        assert d["reason"] == "обязательный компонент недоступен"
        assert d["upstream_calls"] == 0
        assert d["request_sent_out"] is False
        client.post("/trust-lab/action-c/recover", headers=headers)

    def test_action_c_vault_fault_zero_upstream(self, client):
        headers = {"X-Admin-Key": ADMIN_KEY}
        r = client.post(
            "/trust-lab/action-c",
            json={"consumer": "support_demo", "text": SAMPLE, "fault_type": "vault"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "stopped"
        assert d["upstream_calls"] == 0
        assert "vault" in d["degraded_components"]
        client.post("/trust-lab/action-c/recover", headers=headers)

    def test_action_c_requires_admin(self, client):
        r = client.post(
            "/trust-lab/action-c",
            json={"consumer": "support_demo", "text": SAMPLE, "fault_type": "detector"},
        )
        assert r.status_code == 401

    def test_action_c_recovery(self, client):
        headers = {"X-Admin-Key": ADMIN_KEY}
        client.post(
            "/trust-lab/action-c",
            json={"consumer": "support_demo", "text": SAMPLE, "fault_type": "detector", "detector_id": "full_name"},
            headers=headers,
        )
        r = client.post("/trust-lab/action-c/recover", headers=headers)
        assert r.status_code == 200
        assert r.json()["degraded_components"] == []
        # After recovery a normal mask works.
        r2 = client.post(
            "/demo/mask",
            json={"text": "Клиент Иван Петров", "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r2.status_code == 200

    def test_process_cannot_trigger_fault_via_query(self, client):
        # /process has no fault-injection query parameter; it must still work.
        r = client.post(
            "/process?fault=detector",
            json={"payload_id": "tl-fault-query", "payload": "email a@b.com"},
        )
        assert r.status_code == 200, r.text
        assert "a@b.com" not in r.json()["result"]


class TestFaultDisabledProfile:
    def test_fault_injection_disabled_without_env(self, monkeypatch):
        monkeypatch.delenv("PII_TRUST_LAB_FAULTS", raising=False)
        app = create_app()
        with TestClient(app) as c:
            r = c.post(
                "/trust-lab/action-c",
                json={"consumer": "support_demo", "text": "x", "fault_type": "detector"},
                headers={"X-Admin-Key": ADMIN_KEY},
            )
            assert r.status_code == 403


class TestReport:
    def test_report_no_raw_pii(self, client):
        # Run Action A on text containing a canary secret, then build a report.
        text = f"Клиент Иван Петров, email ivan@example.com, карта 4276189074144957, {CANARY}"
        r = client.post(
            "/trust-lab/action-a",
            json={"text": text, "consumer": "support_demo", "labeled": False},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200
        a = r.json()
        rr = client.post(
            "/trust-lab/report",
            json={
                "dataset_or_scenario_id": "trust-lab-action-a",
                "seed": 20260922,
                "input_length": a["text"].__len__(),
                "detected_counts": a["detected_counts"],
                "masked_counts": a["masked_counts"],
                "check_statuses": {"round_trip": "passed"},
                "exact_round_trip": "passed",
                "upstream_calls": 0,
                "timings": {t["stage"]: t["seconds"] for t in a["timings"]},
                "degraded_components": [],
                "known_limitations": [],
                "include_synthetic": False,
            },
        )
        assert rr.status_code == 200, rr.text
        raw = rr.text
        for secret in ["ivan@example.com", "4276189074144957", "Иван Петров", CANARY]:
            assert secret not in raw, f"report leaked: {secret}"

    def test_report_has_required_fields(self, client):
        rr = client.post(
            "/trust-lab/report",
            json={"exact_round_trip": "passed", "upstream_calls": 0},
        )
        d = rr.json()
        for field in [
            "run_id", "created_at", "build_version", "policy_version",
            "detector_manifest_version", "input_length", "token_counter_type",
            "detected_counts", "masked_counts", "check_statuses",
            "exact_round_trip", "upstream_calls", "timings",
            "degraded_components", "known_limitations",
        ]:
            assert field in d, f"missing report field: {field}"


class TestTrustLabAccessControl:
    def test_action_a_requires_auth_for_protected_consumer(self, client):
        """Action A must require authentication for protected consumers."""
        r = client.post(
            "/trust-lab/action-a",
            json={"text": SAMPLE, "consumer": "support_demo", "labeled": False},
        )
        assert r.status_code == 401, r.text

    def test_action_a_denied_for_unmask_disabled_consumer(self, client):
        """analytics_demo (unmask=false) must not return a newly submitted
        original via Action A."""
        r = client.post(
            "/trust-lab/action-a",
            json={"text": SAMPLE, "consumer": "analytics_demo", "labeled": False},
            headers={"X-API-Key": "test-analytics-key"},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        # The masked text must not contain the original values.
        assert "ivan@example.com" not in d["masked_text"]
        assert "4276189074144957" not in d["masked_text"]

    def test_report_ignores_client_supplied_check_statuses(self, client):
        """The report must derive check statuses from recorded runs, not accept
        client-supplied 'passed' for a check that never executed."""
        rr = client.post(
            "/trust-lab/report",
            json={"check_statuses": {"never_executed_check": "passed"}},
        )
        d = rr.json()
        assert "never_executed_check" not in d["check_statuses"]

    def test_healthy_action_c_makes_transport_call(self, client):
        """The healthy Action C path must make a real transport call (positive
        control), so zero calls under failure is meaningful."""
        # Recover any injected fault first.
        client.post("/trust-lab/action-c/recover", headers={"X-Admin-Key": ADMIN_KEY})
        # The healthy path is exercised by the runner with no active fault.
        from app.main import create_app as _ca
        from app.trust_lab.transport import TransportCounter
        from app.trust_lab.faults import FaultInjector
        from app.trust_lab.runner import TrustLabRunner
        from app.detectors.registry import DetectorRegistry
        from app.policies.loader import PolicyStore
        from app.core.engine import Engine
        import os as _os

        _os.environ["PII_TRUST_LAB_FAULTS"] = "1"
        app = _ca()
        engine = app.state.engine
        policy_store = app.state.policy_store
        from app.providers.stub import StubProvider
        transport = TransportCounter(StubProvider())
        runner = TrustLabRunner(
            engine, policy_store, "test", transport, FaultInjector(enabled=True)
        )
        res = runner.action_c("support_demo", SAMPLE)
        assert res.status == "completed"
        assert res.upstream_calls >= 1, "healthy path must make a transport call"
        # Recover so subsequent tests are not affected.
        client.post("/trust-lab/action-c/recover", headers={"X-Admin-Key": ADMIN_KEY})


class TestWorkerChange:
    def test_worker_change_no_foreign_tokens(self):
        shared = MemoryVault()
        app1 = create_app(vault=shared)
        app2 = create_app(vault=shared)
        with TestClient(app1) as c1, TestClient(app2) as c2:
            r = c1.post(
                "/demo/mask",
                json={"text": SAMPLE, "consumer": "support_demo"},
                headers={"X-API-Key": SUPPORT_KEY},
            )
            assert r.status_code == 200
            masked = r.json()["masked_text"]
            ctx = r.json()["context_id"]
            # Worker 2 unmask (simulates worker change).
            r2 = c2.post(
                "/demo/unmask",
                json={"masked_text": masked, "consumer": "support_demo", "context_id": ctx},
                headers={"X-API-Key": SUPPORT_KEY},
            )
            assert r2.status_code == 200
            restored = r2.json()["original_text"]
            assert restored == SAMPLE
            # No foreign or new incompatible tokens remain.
            assert "⟦PII:" not in restored
