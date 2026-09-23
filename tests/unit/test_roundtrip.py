"""Round-trip tests: unmask(mask(x)) == x."""
from app.core.engine import Engine
from app.detectors.address import AddressDetector
from app.detectors.card_security import CardholderNameDetector, CvvDetector, PinDetector
from app.detectors.dates import BirthDateDetector
from app.detectors.documents import (
    DriverLicenseDetector,
    PassportIssueDateDetector,
    PassportIssuerDetector,
)
from app.detectors.identity import BirthPlaceDetector, CitizenshipDetector
from app.detectors.person import FullNameDetector
from app.detectors.structured import (
    CardDetector,
    DepartmentCodeDetector,
    EmailDetector,
    InnDetector,
    PassportDetector,
    PhoneDetector,
)

DETECTORS = [
    EmailDetector(),
    PhoneDetector(),
    CardDetector(),
    PassportDetector(),
    FullNameDetector(),
    BirthDateDetector(),
    InnDetector(),
    DepartmentCodeDetector(),
    AddressDetector(),
    BirthPlaceDetector(),
    CitizenshipDetector(),
    PassportIssuerDetector(),
    PassportIssueDateDetector(),
    DriverLicenseDetector(),
    CvvDetector(),
    PinDetector(),
    CardholderNameDetector(),
]


def _engine():
    return Engine(DETECTORS)


class TestRoundTrip:
    def test_basic(self):
        eng = _engine()
        text = "Клиент Иван Иванович Петров, email ivan.petrov@example.com, тел +7 912 345-67-89"
        result = eng.mask(text, "ns", "ctx")
        restored = eng.unmask(result.masked_text, result.token_mapping)
        assert restored == text

    def test_no_pii_original_equals_masked(self):
        eng = _engine()
        text = "Сегодня хорошая погода в городе"
        result = eng.mask(text, "ns", "ctx")
        assert result.masked_text == text
        assert result.token_mapping == {}

    def test_repeated_values_stable(self):
        eng = _engine()
        text = "email a@b.com и ещё раз a@b.com"
        result = eng.mask(text, "ns", "ctx")
        # Same value -> same token.
        tokens = [t for t in result.token_mapping]
        assert len(tokens) == 1
        restored = eng.unmask(result.masked_text, result.token_mapping)
        assert restored == text

    def test_emoji_before_full_name(self):
        eng = _engine()
        text = "😀 Клиент Иван Петров"
        result = eng.mask(text, "ns", "ctx")
        restored = eng.unmask(result.masked_text, result.token_mapping)
        assert restored == text

    def test_emoji_before_passport(self):
        eng = _engine()
        text = "😀 паспорт серия 0318 номер 123456"
        result = eng.mask(text, "ns", "ctx")
        restored = eng.unmask(result.masked_text, result.token_mapping)
        assert restored == text

    def test_unknown_token_not_revealed(self):
        eng = _engine()
        text = "текст с ⟦PII:EMAIL:fake-token⟧ внутри"
        result = eng.mask(text, "ns", "ctx")
        # The fake token is not a registered token; it stays as-is.
        assert "fake-token" in result.masked_text

    def test_opaque_marker_no_collision_with_original(self):
        """Opaque markers must not collide with markers already in the input."""
        eng = _engine()
        text = "[REDACTED-0] Email: alpha@example.com; Email: beta@example.com"
        result = eng.mask(text, "ns", "ctx", mask_action="opaque_token_full")
        # Both emails must get DISTINCT markers, and neither may collide with
        # the pre-existing [REDACTED-0] in the input.
        markers = [t for t in result.token_mapping]
        assert len(markers) == 2, f"expected 2 distinct markers, got {markers}"
        assert len(set(markers)) == 2, f"markers collided: {markers}"
        assert "[REDACTED-0]" not in markers
        restored = eng.unmask(result.masked_text, result.token_mapping)
        assert restored == text

    def test_opaque_marker_distinct_for_repeated_values(self):
        """Repeated values in opaque mode must still get distinct markers."""
        eng = _engine()
        text = "Email: alpha@example.com; Email: alpha@example.com"
        result = eng.mask(text, "ns", "ctx", mask_action="opaque_token_full")
        markers = [t for t in result.token_mapping]
        assert len(markers) == 2
        assert len(set(markers)) == 2
        restored = eng.unmask(result.masked_text, result.token_mapping)
        assert restored == text

    def test_maskresult_repr_does_not_expose_originals(self):
        """MaskResult repr must never contain original sensitive values (D12)."""
        eng = _engine()
        text = "Клиент Иван Петров, email ivan.petrov@example.com"
        result = eng.mask(text, "ns", "ctx")
        r = repr(result)
        assert "ivan.petrov@example.com" not in r
        assert "Иван Петров" not in r
        assert "masked_text_len" in r
