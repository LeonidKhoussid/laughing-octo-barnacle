"""Verify Detection repr does not leak original values."""
from app.core.models import Decision, Detection, Signal, Span


class TestReprSafety:
    def test_repr_does_not_contain_original_value(self):
        det = Detection(
            entity_id="e1",
            category="EMAIL",
            sensitive_spans=(Span(0, 10),),
            signals=(Signal("email_structure", 1.0),),
            detector_id="email",
            detector_version="1.0.0",
            score=0.95,
            decision=Decision.MASK,
            rule_id="email-structure",
        )
        # The Detection has no original_value field at all.
        assert not hasattr(det, "original_value")
        r = repr(det)
        assert "original_value" not in r
        # repr contains only safe metadata.
        assert "EMAIL" in r
        assert "email" in r
