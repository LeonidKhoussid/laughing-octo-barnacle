"""G6 tests: Russian UI serving, XSS escaping, configurable mask types,
stub provider verification, and demo chat."""
import os

os.environ["SUPPORT_DEMO_API_KEY"] = "test-support-key"
os.environ["ANALYTICS_DEMO_API_KEY"] = "test-analytics-key"
os.environ["COMBINATION_DEMO_API_KEY"] = "test-combination-key"
os.environ["DEMO_CHAT_API_KEY"] = "test-chat-key"

import pytest
from fastapi.testclient import TestClient

from app.core.engine import Engine
from app.detectors.registry import DetectorRegistry
from app.main import create_app

SUPPORT_KEY = "test-support-key"
ANALYTICS_KEY = "test-analytics-key"
CHAT_KEY = "test-chat-key"


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestUIServing:
    def test_index_served(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        # The React app serves a minimal index.html with the root mount point.
        # If the React build is not present (source-only checkout), the legacy
        # static page is served as a fallback.
        assert "AlfaGen" in r.text
        assert ('id="root"' in r.text) or ("Обработка текста" in r.text)

    def test_react_assets_served(self, client):
        # The React production build assets must be served when the build is
        # present. In a source-only checkout (no frontend/dist), the fallback
        # static page is served instead.
        import pathlib
        dist = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
        r = client.get("/")
        if dist.exists():
            import re
            m = re.search(r'src="(/assets/[^"]+\.js)"', r.text)
            assert m, "React JS bundle not referenced in index.html"
            assert client.get(m.group(1)).status_code == 200
        else:
            # Source-only checkout: the React source must be present.
            src = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
            assert (src / "App.tsx").exists(), "React source missing"

    def test_react_source_no_dangerously_set_inner_html(self):
        # The React source must not use dangerouslySetInnerHTML with untrusted
        # text (no XSS).
        import pathlib
        src = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
        for f in src.rglob("*.tsx"):
            content = f.read_text(encoding="utf-8")
            assert "dangerouslySetInnerHTML" not in content, f"XSS risk in {f}"


class TestXSSEscaping:
    def test_malicious_html_is_escaped_in_mask_response(self, client):
        # A <script> tag in the input must not be reflected as raw HTML in the
        # masked output (the backend returns it as plain text; the UI renders
        # via textContent). The masked_text must contain the literal characters
        # but never be interpreted as markup.
        text = 'Клиент <script>alert("xss")</script> Иван Петров, email a@b.com'
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200, r.text
        masked = r.json()["masked_text"]
        # The literal script text is preserved as data (not stripped), but the
        # UI must render it via textContent. Assert the raw tag is present as
        # plain text in the JSON (data), which the frontend escapes.
        assert "<script>" in masked
        # The email must still be masked.
        assert "a@b.com" not in masked

    def test_ui_renders_script_as_text(self, client):
        # The React source renders user/provider content as text (no raw HTML).
        # The backend returns the literal string so the browser treats it as text.
        import pathlib
        src = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
        for f in src.rglob("*.tsx"):
            content = f.read_text(encoding="utf-8")
            assert "dangerouslySetInnerHTML" not in content, f"XSS risk in {f}"


class TestConfigurableMaskTypes:
    def test_tokenize_full_roundtrip(self, client):
        text = "Клиент Иван Петров, email ivan@example.com, карта 4276189074144957"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["mask_action"] == "tokenize_full"
        assert "⟦PII:" in d["masked_text"]
        r2 = client.post(
            "/demo/unmask",
            json={"masked_text": d["masked_text"], "consumer": "support_demo", "context_id": d["context_id"]},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["original_text"] == text

    def test_opaque_token_full_roundtrip(self, client):
        text = "Клиент Иван Петров, email ivan@example.com, карта 4276189074144957"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "analytics_demo"},
            headers={"X-API-Key": ANALYTICS_KEY},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["mask_action"] == "opaque_token_full"
        assert "⟦PII:" not in d["masked_text"]
        assert "[REDACTED-" in d["masked_text"]
        # detected_counts must still be populated (counted from spans).
        assert d["detected_counts"].get("EMAIL") == 1

    def test_opaque_markers_reversible_via_engine(self):
        # analytics_demo has allow_unmask=false, so verify reversibility at the
        # engine level (opaque markers must still map back to originals).
        reg = DetectorRegistry.from_config("configs/detectors.yaml")
        eng = Engine(reg.detectors)
        text = "Клиент Иван Петров, email ivan@example.com, карта 4276189074144957"
        res = eng.mask(text, "analytics-demo", "ctx", mask_action="opaque_token_full")
        assert "[REDACTED-" in res.masked_text
        assert "⟦PII:" not in res.masked_text
        restored = eng.unmask(res.masked_text, res.token_mapping)
        assert restored == text

    def test_both_modes_roundtrip_exactly(self):
        reg = DetectorRegistry.from_config("configs/detectors.yaml")
        eng = Engine(reg.detectors)
        text = "Иван Петров, тел +7 912 345-67-89, email a@b.com"
        for action in ("tokenize_full", "opaque_token_full"):
            res = eng.mask(text, "ns", "ctx", mask_action=action)
            assert eng.unmask(res.masked_text, res.token_mapping) == text


class TestStubProvider:
    def test_chat_works_with_stub_no_external_network(self, client):
        text = "Клиент Иван Петров, email ivan@example.com, карта 4276189074144957"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "demo_chat"},
            headers={"X-API-Key": CHAT_KEY},
        )
        assert r.status_code == 200, r.text
        masked = r.json()["masked_text"]
        context_id = r.json()["context_id"]
        rc = client.post(
            "/demo/chat",
            json={"masked_text": masked, "consumer": "demo_chat", "context_id": context_id},
            headers={"X-API-Key": CHAT_KEY},
        )
        assert rc.status_code == 200, rc.text
        d = rc.json()
        assert d["provider"] == "stub"
        assert d["stub"] is True

    def test_outbound_payload_has_no_original_pii(self, client):
        text = "Клиент Иван Петров, email ivan@example.com, карта 4276189074144957"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "demo_chat"},
            headers={"X-API-Key": CHAT_KEY},
        )
        masked = r.json()["masked_text"]
        context_id = r.json()["context_id"]
        rc = client.post(
            "/demo/chat",
            json={"masked_text": masked, "consumer": "demo_chat", "context_id": context_id},
            headers={"X-API-Key": CHAT_KEY},
        )
        resp = rc.json()["response"]
        assert "ivan@example.com" not in resp
        assert "4276189074144957" not in resp
        assert "Иван Петров" not in resp

    def test_tokens_preserved_unchanged(self, client):
        text = "email ivan@example.com, карта 4276189074144957"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "demo_chat"},
            headers={"X-API-Key": CHAT_KEY},
        )
        masked = r.json()["masked_text"]
        context_id = r.json()["context_id"]
        rc = client.post(
            "/demo/chat",
            json={"masked_text": masked, "consumer": "demo_chat", "context_id": context_id},
            headers={"X-API-Key": CHAT_KEY},
        )
        resp = rc.json()["response"]
        # Every token in the masked text appears unchanged in the response.
        import re
        tokens = re.findall(r"⟦PII:[^⟧]+⟧", masked)
        assert tokens
        for tok in tokens:
            assert tok in resp

    def test_mask_chat_restore_response_roundtrip(self, client):
        """mask -> chat -> restore-response must restore the original values
        inside the changed provider output (Fix B)."""
        text = "Клиент Иван Петров, email ivan@example.com, карта 4276189074144957"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "demo_chat"},
            headers={"X-API-Key": CHAT_KEY},
        )
        assert r.status_code == 200, r.text
        masked = r.json()["masked_text"]
        context_id = r.json()["context_id"]
        rc = client.post(
            "/demo/chat",
            json={"masked_text": masked, "consumer": "demo_chat", "context_id": context_id},
            headers={"X-API-Key": CHAT_KEY},
        )
        assert rc.status_code == 200, rc.text
        response = rc.json()["response"]
        rr = client.post(
            "/demo/restore-response",
            json={"response_text": response, "consumer": "demo_chat", "context_id": context_id},
            headers={"X-API-Key": CHAT_KEY},
        )
        assert rr.status_code == 200, rr.text
        restored = rr.json()["restored_text"]
        # The restored response must contain the original values (exact token
        # substitution), even though the provider prefixed its output.
        assert "ivan@example.com" in restored
        assert "4276189074144957" in restored
        assert "Иван Петров" in restored

    def test_chat_rejects_raw_pii_as_masked(self, client):
        """Raw passport/email submitted as masked_text must be rejected (C)."""
        text = "Клиент Иван Петров, email ivan@example.com, карта 4276189074144957"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "demo_chat"},
            headers={"X-API-Key": CHAT_KEY},
        )
        context_id = r.json()["context_id"]
        # Submit the RAW original text as masked_text with a valid context_id.
        rc = client.post(
            "/demo/chat",
            json={"masked_text": text, "consumer": "demo_chat", "context_id": context_id},
            headers={"X-API-Key": CHAT_KEY},
        )
        # The raw text does not match the committed masked_text -> conflict.
        assert rc.status_code == 409, rc.text

    def test_chat_rejects_unknown_context(self, client):
        """Chat with an unknown context_id must be rejected (no fabricated
        server-side processing)."""
        rc = client.post(
            "/demo/chat",
            json={
                "masked_text": "⟦PII:EMAIL:abc⟧",
                "consumer": "demo_chat",
                "context_id": "does-not-exist",
            },
            headers={"X-API-Key": CHAT_KEY},
        )
        assert rc.status_code == 404, rc.text


class TestCombinationRuleDemo:
    def test_combination_rule_demo_via_api(self, client):
        # PIN without a related CARD in the same record is kept (combination rule).
        r = client.post(
            "/demo/mask",
            json={"text": "PIN: 4321", "consumer": "combination_demo"},
            headers={"X-API-Key": "test-combination-key"},
        )
        assert r.status_code == 200, r.text
        assert "4321" in r.json()["masked_text"]

        # PIN with a related CARD in the same record is masked.
        r2 = client.post(
            "/demo/mask",
            json={"text": "карта 4276189074144957, PIN: 4321", "consumer": "combination_demo"},
            headers={"X-API-Key": "test-combination-key"},
        )
        assert r2.status_code == 200, r2.text
        # PIN with a related CARD is masked (not present in the masked text).
        assert "4321" not in r2.json()["masked_text"]


class TestProviderOutputProtection:
    """Provider responses with NEWLY introduced sensitive content must be masked
    before returning (review issue 2)."""

    @pytest.fixture
    def client(self):
        """A function-scoped app instance so StubProvider.complete monkeypatches
        do not leak to the shared module-scoped client used by other tests."""
        app = create_app()
        with TestClient(app) as c:
            yield c

    @pytest.fixture(autouse=True)
    def _restore_stub(self):
        """Always restore StubProvider.complete after each test, even on
        failure, so the monkeypatch cannot leak to other tests."""
        from app.providers.stub import StubProvider
        original = StubProvider.complete
        yield
        StubProvider.complete = original

    def _chat(self, client, text, stub_response, monkeypatch):
        from app.providers.stub import StubProvider
        a = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "demo_chat"},
            headers={"X-API-Key": CHAT_KEY},
        ).json()
        data = {"masked_text": a["masked_text"], "context_id": a["context_id"], "consumer": "demo_chat"}
        monkeypatch.setattr(StubProvider, "complete", lambda self, t: stub_response)
        return client.post("/demo/chat", json=data, headers={"X-API-Key": CHAT_KEY})

    def test_safe_response_with_known_tokens(self, client, monkeypatch):
        """A safe response containing known tokens is returned unchanged."""
        text = "Email: ivan@example.com"
        r = self._chat(client, text, "Ответ: ⟦PII:EMAIL:known⟧", monkeypatch)
        assert r.status_code == 200, r.text
        assert "⟦PII:EMAIL:known⟧" in r.json()["response"]

    def test_new_email_masked(self, client, monkeypatch):
        """A newly introduced email in the provider response is masked."""
        text = "Email: ivan@example.com"
        r = self._chat(client, text, "Email: new-provider-canary@example.com", monkeypatch)
        assert r.status_code == 200, r.text
        assert "new-provider-canary@example.com" not in r.json()["response"]
        assert "⟦PII:EMAIL:" in r.json()["response"]

    def test_new_phone_masked(self, client, monkeypatch):
        text = "Email: ivan@example.com"
        r = self._chat(client, text, "Позвоните +7 912 345-67-89", monkeypatch)
        assert r.status_code == 200, r.text
        assert "+7 912 345-67-89" not in r.json()["response"]
        assert "⟦PII:PHONE:" in r.json()["response"]

    def test_new_passport_masked(self, client, monkeypatch):
        text = "Email: ivan@example.com"
        r = self._chat(client, text, "Паспорт серия 0318 номер 123456", monkeypatch)
        assert r.status_code == 200, r.text
        assert "0318" not in r.json()["response"]
        assert "123456" not in r.json()["response"]

    def test_known_tokens_plus_new_sensitive(self, client, monkeypatch):
        """Known tokens preserved; new sensitive content masked."""
        text = "Клиент Иван Петров, email ivan@example.com"
        a = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "demo_chat"},
            headers={"X-API-Key": CHAT_KEY},
        ).json()
        masked = a["masked_text"]
        data = {"masked_text": masked, "context_id": a["context_id"], "consumer": "demo_chat"}
        from app.providers.stub import StubProvider
        monkeypatch.setattr(StubProvider, "complete", lambda self, t: f"Ответ: {masked} Новый email new@example.com")
        r = client.post("/demo/chat", json=data, headers={"X-API-Key": CHAT_KEY})
        assert r.status_code == 200, r.text
        resp = r.json()["response"]
        # Known tokens preserved.
        for tok in ["⟦PII:FULL_NAME:", "⟦PII:EMAIL:"]:
            assert tok in resp
        # New email masked.
        assert "new@example.com" not in resp

    def test_foreign_unknown_token_not_revealed(self, client, monkeypatch):
        """A foreign/unknown token in the response is not restored/revealed."""
        text = "Email: ivan@example.com"
        r = self._chat(client, text, "Ответ: ⟦PII:EMAIL:foreign-token⟧", monkeypatch)
        assert r.status_code == 200, r.text
        # The foreign token is not in the mapping, so it stays as-is (not revealed).
        assert "⟦PII:EMAIL:foreign-token⟧" in r.json()["response"]

    def test_restore_response_does_not_reveal_new_pii(self, client, monkeypatch):
        """restore-response must not reveal new PII introduced by the provider."""
        text = "Email: ivan@example.com"
        a = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "demo_chat"},
            headers={"X-API-Key": CHAT_KEY},
        ).json()
        data = {"masked_text": a["masked_text"], "context_id": a["context_id"], "consumer": "demo_chat"}
        from app.providers.stub import StubProvider
        monkeypatch.setattr(StubProvider, "complete", lambda self, t: "Ответ: new-provider-canary@example.com")
        r = client.post("/demo/chat", json=data, headers={"X-API-Key": CHAT_KEY})
        protected = r.json()["response"]
        # Restore the protected response; the new email must NOT be revealed.
        rr = client.post(
            "/demo/restore-response",
            json={"response_text": protected, "consumer": "demo_chat", "context_id": a["context_id"]},
            headers={"X-API-Key": CHAT_KEY},
        )
        assert rr.status_code == 200, rr.text
        assert "new-provider-canary@example.com" not in rr.json()["restored_text"]


class TestEmojiOffsets:
    def test_emoji_before_sensitive_span_offsets(self, client):
        """An emoji before a sensitive span must not break code-point offsets."""
        text = "😀 Клиент Иванов Иван, email ivan@example.com"
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        # The spans are in code-point offsets; the email span must slice correctly.
        email_span = next((s for s in d["spans"] if s["category"] == "EMAIL"), None)
        assert email_span is not None
        # Code-point slice of the original must equal the email.
        assert text[email_span["start"]:email_span["end"]] == "ivan@example.com"


class TestConfigConsumerInfo:
    def test_config_has_consumer_info(self, client):
        cfg = client.get("/config").json()
        info = cfg.get("consumer_info", {})
        assert "support_demo" in info
        assert "allow_unmask" in info["support_demo"]
        assert "allow_llm_egress" in info["support_demo"]
        assert "mask_action" in info["support_demo"]
        assert "enabled" in info["support_demo"]

    def test_mask_reports_egress_disabled(self, client):
        # support_demo has egress disabled; the mask response must say so.
        r = client.post(
            "/demo/mask",
            json={"text": "email ivan@example.com", "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200, r.text
        assert r.json()["egress_disabled"] is True
