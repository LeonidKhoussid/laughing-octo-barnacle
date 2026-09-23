"""Public references and private names use the same rules in both HTTP APIs."""
from uuid import uuid4
import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="module")
def context_client():
    with TestClient(create_app()) as client:
        yield client


@pytest.mark.parametrize(("text", "expected"), [
    ("Поэт Александр Сергеевич Пушкин написал роман «Евгений Онегин».", []),
    ("Александр Сергеевич Пушкин родился в Москве.", []),
    ("Поэт Иван Петров родился 12 апреля 1990 года в Москве.", []),
    ("Клиент Александер Сергеевич Пушкин, телефон +7 918 123-45-67, карта 4276 1234 5678 9012.",
     [("FULL_NAME", "Александер Сергеевич Пушкин"), ("PHONE", "+7 918 123-45-67"), ("CARD", "4276 1234 5678 9012")]),
    ("Клиент Александр Сергеевич Пушкин родился в Москве.",
     [("FULL_NAME", "Александр Сергеевич Пушкин"), ("BIRTH_PLACE", "Москве")]),
    ("Клиент, поэт Иван Петров родился 12 апреля 1990 года.",
     [("FULL_NAME", "Иван Петров"), ("BIRTH_DATE", "12 апреля 1990")]),
    ("Поэт Пушкин родился в Москве. Клиент Иван Петров родился в Казани.",
     [("FULL_NAME", "Иван Петров"), ("BIRTH_PLACE", "Казани")]),
    ("Клиент Иван Иванович Петров читает произведения Александра Сергеевича Пушкина. Телефон клиента: +7 918 123-45-67.",
     [("FULL_NAME", "Иван Иванович Петров"), ("PHONE", "+7 918 123-45-67")]),
    ("Адрес отделения: г. Москва, ул. Тверская, д. 10. Адрес клиента: г. Казань, ул. Баумана, д. 7, кв. 3.",
     [("ADDRESS", "Казань"), ("ADDRESS", "Баумана"), ("ADDRESS", "7"), ("ADDRESS", "3")]),
])
def test_context_spans_and_exact_http_roundtrip(context_client, text, expected):
    mask = context_client.post("/demo/mask", json={"text": text, "consumer": "autocheck"})
    assert mask.status_code == 200
    data = mask.json()
    assert [(s["category"], text[s["start"]:s["end"]]) for s in data["spans"]] == expected
    restored = context_client.post("/demo/unmask", json={
        "consumer": "autocheck", "context_id": data["context_id"], "masked_text": data["masked_text"],
    })
    assert restored.status_code == 200
    assert restored.json()["original_text"] == text

    payload_id = uuid4().hex
    official = context_client.post("/process", json={"payload": text, "payload_id": payload_id})
    assert official.status_code == 200
    masked = official.json()["result"]
    # Compare literal surrounding text and whole token spans. Short originals
    # such as a house number may occur by chance inside an opaque random token.
    pattern = []
    cursor = 0
    for category, value in expected:
        start = text.index(value, cursor)
        pattern.extend((re.escape(text[cursor:start]), rf"⟦PII:{category}:[A-Za-z0-9_-]+⟧"))
        cursor = start + len(value)
    pattern.append(re.escape(text[cursor:]))
    assert re.fullmatch("".join(pattern), masked)
    restored = context_client.post("/process", json={"payload": masked, "payload_id": payload_id})
    assert restored.status_code == 200
    assert restored.json() == {"result": text}
