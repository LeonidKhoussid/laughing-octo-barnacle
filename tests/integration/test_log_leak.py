"""Verify no PII leaks into logs."""
import io
import logging

from app.observability.logs import SafeLogger


class TestLogLeak:
    def test_logs_do_not_contain_original_values(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("alfagen-test")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)

        safe = SafeLogger("alfagen-test")
        original = "ivan.petrov@example.com"
        masked = "⟦PII:EMAIL:abc123⟧"
        safe.info(
            "mask completed",
            request_id="req-1",
            operation="mask",
            consumer="support_demo",
            policy_version="baseline-001",
            stage="completed",
            detected_counts={"EMAIL": 1},
            degraded=False,
            result="success",
        )
        # The logger must never receive original/masked body, but even if it did,
        # the allowlist drops them.
        safe.info("mask completed", original=original, masked=masked)

        output = stream.getvalue()
        assert original not in output
        assert masked not in output
        assert "ivan.petrov" not in output
        assert "req-1" in output  # request_id is allowlisted
        assert "support_demo" in output  # consumer is allowlisted


class TestLogLeakNewCategories:
    def test_new_category_values_do_not_leak(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("alfagen-newcat")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)

        safe = SafeLogger("alfagen-newcat")
        # Even if a caller mistakenly passes original values, the allowlist drops them.
        safe.info(
            "mask completed",
            request_id="req-newcat",
            operation="mask",
            consumer="support_demo",
            policy_version="baseline-001",
            stage="completed",
            detected_counts={
                "BIRTH_PLACE": 1,
                "CITIZENSHIP": 1,
                "PASSPORT_ISSUER": 1,
                "PASSPORT_ISSUE_DATE": 1,
                "DRIVER_LICENSE": 1,
                "CVV": 1,
                "PIN": 1,
                "CARDHOLDER_NAME": 1,
            },
            degraded=False,
            result="success",
        )
        # Attempt to leak values via non-allowlisted fields.
        safe.info(
            "mask completed",
            birth_place="Москва",
            citizenship="Российская Федерация",
            issuer="ОВД района",
            issue_date="15.03.2015",
            driver_license="77 12 345678",
            cvv="123",
            pin="4321",
            cardholder="Иван Петров",
        )

        output = stream.getvalue()
        for value in ["Москва", "Российская Федерация", "ОВД района", "15.03.2015",
                      "77 12 345678", "123", "4321", "Иван Петров"]:
            assert value not in output, f"leaked: {value}"
        assert "req-newcat" in output
        assert "BIRTH_PLACE" in output  # category names are safe metadata
