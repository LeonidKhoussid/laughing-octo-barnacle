"""G5 functional test: a ~100k-token document is processed end-to-end.

The tokenizer used here is APPROXIMATE (chars/4), marked `estimated` (R61,
section 14.4). It is NOT the organizers' tokenizer. The engine processes the
whole document in one pass (no silent truncation), so sensitive fragments at the
beginning, middle, and end must all be masked, and round-trip must be exact.
"""
import os

os.environ["SUPPORT_DEMO_API_KEY"] = "test-support-key"

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

SUPPORT_KEY = "test-support-key"

# Approximate tokenizer: ~4 chars per token (English/Russian mixed). Marked
# `estimated` — not the organizers' tokenizer (R61).
CHARS_PER_TOKEN = 4.0
TARGET_TOKENS = 100_000


def approx_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def build_100k_text() -> str:
    """Build a ~100k-token document with sensitive fragments at start/mid/end."""
    filler = (
        "Это обычный текст документа без персональных данных. "
        "Здесь описывается некоторая деловая информация и контекст. "
    )
    start_frag = "Клиент Иван Иванович Петров, email ivan.petrov@example.com, телефон +7 912 345-67-89. "
    mid_frag = "паспорт серия 0318 номер 123456, карта 4276189074144957. "
    end_frag = "дата рождения 12 апреля 1990 года, адрес г. Москва, ул. Тверская, д. 15, кв. 42."

    # Build filler to reach ~100k tokens. Use a small margin so the final
    # document is guaranteed >= 100k tokens even with the mid/end fragments.
    parts = [start_frag]
    while approx_tokens("".join(parts)) < TARGET_TOKENS // 2:
        parts.append(filler)
    parts.append(mid_frag)
    while approx_tokens("".join(parts)) < TARGET_TOKENS + 200:
        parts.append(filler)
    parts.append(end_frag)
    return "".join(parts)


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


class Test100kFunctional:
    def test_100k_document_processed_without_crash(self, client):
        text = build_100k_text()
        n_tokens = approx_tokens(text)
        assert n_tokens >= 100_000, f"expected >=100k tokens, got {n_tokens}"

        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        masked = data["masked_text"]
        context_id = data["context_id"]

        # Sensitive fragments at beginning, middle, and end must all be masked.
        assert "ivan.petrov@example.com" not in masked
        assert "+7 912 345-67-89" not in masked
        assert "0318" not in masked
        assert "123456" not in masked
        assert "4276189074144957" not in masked
        assert "12 апреля 1990" not in masked
        assert "Тверская" not in masked

        # Round-trip must be exact.
        r2 = client.post(
            "/demo/unmask",
            json={"masked_text": masked, "consumer": "support_demo", "context_id": context_id},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["original_text"] == text

    def test_end_of_document_fragment_is_masked(self, client):
        # The end-of-document fragment must be masked (no silent truncation).
        text = build_100k_text()
        r = client.post(
            "/demo/mask",
            json={"text": text, "consumer": "support_demo"},
            headers={"X-API-Key": SUPPORT_KEY},
        )
        assert r.status_code == 200
        masked = r.json()["masked_text"]
        # The end fragment's sensitive values must not appear anywhere.
        assert "12 апреля 1990" not in masked
        assert "Тверская" not in masked
        assert "кв. 42" not in masked
