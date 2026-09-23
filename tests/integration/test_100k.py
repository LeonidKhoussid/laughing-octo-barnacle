"""A document of at least 100k actual RuBERT NER tokens is processed end-to-end.

The tokenizer is the pinned active NER tokenizer, not an organizer-specified
tokenizer. Sensitive fragments at start/middle/end and exact restoration are
checked. The old chars/4 fixture contained only 60,897 tokens with this tokenizer.
"""
import os
from functools import lru_cache
from pathlib import Path

from tokenizers import Tokenizer

os.environ["SUPPORT_DEMO_API_KEY"] = "test-support-key"

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

SUPPORT_KEY = "test-support-key"

TARGET_TOKENS = 100_000


@lru_cache(maxsize=1)
def document_tokenizer():
    path = Path(__file__).resolve().parents[2] / "models/semantic/ner/tokenizer.json"
    tokenizer = Tokenizer.from_file(str(path))
    tokenizer.no_padding()
    tokenizer.no_truncation()
    return tokenizer


def actual_tokens(text: str) -> int:
    return len(document_tokenizer().encode(text, add_special_tokens=False).ids)


@lru_cache(maxsize=1)

def build_100k_text() -> str:
    """Build a tokenizer-measured 100k-token document with sensitive fragments at start/mid/end."""
    filler = (
        "Это обычный текст документа без персональных данных. "
        "Здесь описывается некоторая деловая информация и контекст. "
    )
    start_frag = "Клиент Иван Иванович Петров, email ivan.petrov@example.com, телефон +7 912 345-67-89. "
    mid_frag = "паспорт серия 0318 номер 123456, карта 4276189074144957. "
    end_frag = "дата рождения 12 апреля 1990 года, адрес г. Москва, ул. Тверская, д. 15, кв. 42."

    # Construct linearly instead of joining an ever-growing list in a loop.
    repeats = (TARGET_TOKENS + actual_tokens(filler) - 1) // actual_tokens(filler)
    text = start_frag + filler * (repeats // 2) + mid_frag + filler * (repeats - repeats // 2) + end_frag
    assert actual_tokens(text) >= TARGET_TOKENS
    return text


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


class Test100kFunctional:
    def test_100k_document_processed_without_crash(self, client):
        text = build_100k_text()
        n_tokens = actual_tokens(text)
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
