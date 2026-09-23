"""Real smoke test: mask -> unmask round-trip via the running HTTP service.

Uses synthetic data only (no real PII). Requires the service running on
http://127.0.0.1:8000 with SUPPORT_DEMO_API_KEY set.
"""
from __future__ import annotations

import os
import sys

import httpx

BASE = os.environ.get("PII_SMOKE_BASE", "http://127.0.0.1:8000")
KEY = os.environ.get("SUPPORT_DEMO_API_KEY", "")

SAMPLE = (
    "Клиент Иван Иванович Петров, дата рождения 12 апреля 1990 года, "
    "паспорт серия 0318 номер 123456, email ivan.petrov@example.com, "
    "телефон +7 912 345-67-89, карта 4276189074144957."
)


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=10.0) as client:
        # Health
        r = client.get("/health")
        print("health:", r.status_code, r.json())
        if r.status_code != 200:
            return 1

        # Mask
        r = client.post(
            "/demo/mask",
            json={"text": SAMPLE, "consumer": "support_demo"},
            headers={"X-API-Key": KEY},
        )
        print("mask status:", r.status_code)
        if r.status_code != 200:
            print("mask error:", r.text)
            return 1
        data = r.json()
        masked = data["masked_text"]
        context_id = data["context_id"]
        print("masked:", masked)
        print("detected_counts:", data["detected_counts"])
        print("context_id:", context_id)

        # Unmask
        r2 = client.post(
            "/demo/unmask",
            json={"masked_text": masked, "consumer": "support_demo", "context_id": context_id},
            headers={"X-API-Key": KEY},
        )
        print("unmask status:", r2.status_code)
        if r2.status_code != 200:
            print("unmask error:", r2.text)
            return 1
        restored = r2.json()["original_text"]
        print("restored:", restored)

        ok = restored == SAMPLE
        print("ROUND_TRIP_OK:", ok)
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
