"""All required fields through the real model-enabled /process pipeline."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from scripts.benchmark_process import required_cases, sample_for


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as value:
        yield value


@pytest.mark.parametrize("index", range(len(required_cases())), ids=[row["id"] for row in required_cases()])
def test_required_field_mask_and_restore(client, index):
    sample = sample_for(index, "required-field-regression", ("required",))
    identity = uuid.uuid4().hex
    response = client.post("/process", json={"payload_id": identity, "payload": sample.text})
    assert response.status_code == 200
    masked = response.json()["result"]
    assert sample.accepts_mask(masked)
    assert f"⟦PII:{sample.kind}:" in masked
    restored = client.post("/process", json={"payload_id": identity, "payload": masked})
    assert restored.status_code == 200
    assert restored.json()["result"] == sample.text
