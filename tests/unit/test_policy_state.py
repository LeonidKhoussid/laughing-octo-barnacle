"""Regression tests for state/policy fixes (F): detect_types validation,
disabled autocheck, and retention separation."""
import os

os.environ["SUPPORT_DEMO_API_KEY"] = "test-support-key"

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.policies.loader import PolicyLoadError, load_policy_config
from app.policies.schema import ConsumerPolicy

SUPPORT_KEY = "test-support-key"


class TestDetectTypesValidation:
    def test_unknown_category_rejected(self):
        """A typo like FULL_NMAE must fail clearly, not silently disable
        detection (D04/D07)."""
        with pytest.raises(ValueError):
            ConsumerPolicy(
                name="bad",
                namespace="bad-ns",
                detect_types="FULL_NMAE",
            ).detect_types_set()

    def test_valid_categories_accepted(self):
        p = ConsumerPolicy(
            name="ok",
            namespace="ok-ns",
            detect_types="FULL_NAME, EMAIL, PHONE",
        )
        assert p.detect_types_set() == {"FULL_NAME", "EMAIL", "PHONE"}

    def test_all_required_default(self):
        p = ConsumerPolicy(name="ok", namespace="ok-ns")
        assert p.detect_types_set() == {
            "FULL_NAME", "BIRTH_DATE", "BIRTH_PLACE", "PASSPORT", "CITIZENSHIP",
            "PASSPORT_ISSUER", "DEPARTMENT_CODE", "PASSPORT_ISSUE_DATE",
            "DRIVER_LICENSE", "ADDRESS", "EMAIL", "PHONE", "INN", "CARD",
            "CVV", "PIN", "CARDHOLDER_NAME",
        }

    def test_invalid_config_fails_at_load(self, tmp_path):
        """An invalid detect_types in consumers.yaml must fail at load time."""
        cfg = tmp_path / "consumers.yaml"
        cfg.write_text(
            "schema_version: 1\n"
            "policy_version: baseline-001\n"
            "consumers:\n"
            "  bad:\n"
            "    namespace: bad-ns\n"
            "    detect_types: FULL_NMAE\n",
            encoding="utf-8",
        )
        with pytest.raises(PolicyLoadError):
            load_policy_config(str(cfg))


class TestDisabledAutocheck:
    def test_disabled_autocheck_does_not_process(self, tmp_path):
        """A disabled autocheck consumer must not process requests (D07)."""
        cfg = tmp_path / "consumers.yaml"
        cfg.write_text(
            "schema_version: 1\n"
            "policy_version: baseline-001\n"
            "consumers:\n"
            "  autocheck:\n"
            "    namespace: autocheck\n"
            "    enabled: false\n"
            "    authentication: competition_exception\n",
            encoding="utf-8",
        )
        app = create_app(consumers_path=str(cfg))
        with TestClient(app) as c:
            r = c.post("/process", json={"payload_id": "p1", "payload": "email a@b.com"})
            assert r.status_code == 422  # processing_error, not a successful mask