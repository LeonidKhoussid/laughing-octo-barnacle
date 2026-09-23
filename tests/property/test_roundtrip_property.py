"""Property tests for round-trip on synthetic strings."""
import random

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

ENGINE = Engine(DETECTORS)

# Synthetic PII values (no real PII).
EMAILS = ["ivan.petrov@example.com", "a.b@test.org"]
PHONES = ["+7 912 345-67-89", "8 (495) 123-45-67"]
CARDS = ["4276189074144957", "5555361880738251"]
NAMES = ["Иван Иванович Петров", "Анна Сергеевна Иванова"]
DATES = ["12 апреля 1990 года", "12.04.1990"]
INNS = ["119098818444", "4072864430"]
PASSPORTS = ["паспорт серия 0318 номер 123456"]
BIRTH_PLACES = ["место рождения: г. Москва", "родился в Москве"]
CITIZENSHIPS = ["гражданство: Российская Федерация", "гражданин России"]
ISSUERS = ["паспорт выдан ОВД района", "кем выдан: УФМС России"]
ISSUE_DATES = ["паспорт выдан 15.03.2015", "дата выдачи 15 марта 2015 года"]
DRIVER_LICENSES = ["водительское удостоверение 77 12 345678", "ВУ 7712 345678"]
CVVS = ["CVV 123", "код безопасности 456"]
PINS = ["PIN: 4321", "пин-код 1234"]
CARDHOLDERS = ["держатель карты Иван Петров", "cardholder JOHN SMITH"]


def _random_text(rng):
    parts = []
    for _ in range(rng.randint(1, 4)):
        kind = rng.choice([
            "email", "phone", "card", "name", "date", "inn", "passport",
            "birth_place", "citizenship", "issuer", "issue_date",
            "driver_license", "cvv", "pin", "cardholder",
        ])
        if kind == "email":
            parts.append(f"email {rng.choice(EMAILS)}")
        elif kind == "phone":
            parts.append(f"тел {rng.choice(PHONES)}")
        elif kind == "card":
            parts.append(f"карта {rng.choice(CARDS)}")
        elif kind == "name":
            parts.append(f"Клиент {rng.choice(NAMES)}")
        elif kind == "date":
            parts.append(f"дата рождения {rng.choice(DATES)}")
        elif kind == "inn":
            parts.append(f"ИНН {rng.choice(INNS)}")
        elif kind == "passport":
            parts.append(rng.choice(PASSPORTS))
        elif kind == "birth_place":
            parts.append(rng.choice(BIRTH_PLACES))
        elif kind == "citizenship":
            parts.append(rng.choice(CITIZENSHIPS))
        elif kind == "issuer":
            parts.append(rng.choice(ISSUERS))
        elif kind == "issue_date":
            parts.append(rng.choice(ISSUE_DATES))
        elif kind == "driver_license":
            parts.append(rng.choice(DRIVER_LICENSES))
        elif kind == "cvv":
            parts.append(rng.choice(CVVS))
        elif kind == "pin":
            parts.append(rng.choice(PINS))
        elif kind == "cardholder":
            parts.append(rng.choice(CARDHOLDERS))
    return ", ".join(parts)


class TestRoundTripProperty:
    def test_roundtrip_random(self):
        rng = random.Random(42)
        for _ in range(50):
            text = _random_text(rng)
            result = ENGINE.mask(text, "ns", "ctx")
            restored = ENGINE.unmask(result.masked_text, result.token_mapping)
            assert restored == text, f"round-trip failed for: {text!r}"

    def test_roundtrip_case_variants(self):
        for text in [
            "Клиент Иван Иванович Петров",
            "клиент ИВАН ИВАНОВИЧ ПЕТРОВ",
            "КЛИЕНТ Иван Петров",
        ]:
            result = ENGINE.mask(text, "ns", "ctx")
            assert ENGINE.unmask(result.masked_text, result.token_mapping) == text

    def test_roundtrip_nbsp(self):
        text = "email a@b.com\u00a0и\u00a0телефон\u00a0+7 912 345-67-89"
        result = ENGINE.mask(text, "ns", "ctx")
        assert ENGINE.unmask(result.masked_text, result.token_mapping) == text

    def test_roundtrip_dashes(self):
        text = "телефон +7 912 345-67-89 и карта 4276-1890-7414-4957"
        result = ENGINE.mask(text, "ns", "ctx")
        assert ENGINE.unmask(result.masked_text, result.token_mapping) == text

    def test_roundtrip_emoji_before_span(self):
        text = "😀 Клиент Иван Петров, 😀 паспорт серия 0318 номер 123456"
        result = ENGINE.mask(text, "ns", "ctx")
        assert ENGINE.unmask(result.masked_text, result.token_mapping) == text

    def test_roundtrip_repeated_values(self):
        text = "email a@b.com и ещё раз a@b.com и снова a@b.com"
        result = ENGINE.mask(text, "ns", "ctx")
        assert ENGINE.unmask(result.masked_text, result.token_mapping) == text

    def test_roundtrip_newlines_and_tabs(self):
        text = "email a@b.com\nтелефон +7 912 345-67-89\tкарта 4276189074144957"
        result = ENGINE.mask(text, "ns", "ctx")
        assert ENGINE.unmask(result.masked_text, result.token_mapping) == text
