"""Positive and negative tests for each detector."""
import pytest

from app.detectors.address import AddressDetector
from app.detectors.dates import BirthDateDetector
from app.detectors.person import FullNameDetector
from app.detectors.structured import (
    CardDetector,
    DepartmentCodeDetector,
    EmailDetector,
    InnDetector,
    PassportDetector,
    PhoneDetector,
)


class TestEmail:
    def test_positive(self):
        det = EmailDetector()
        res = det.detect("свяжитесь с ivan.petrov@example.com пожалуйста")
        assert len(res) == 1
        assert res[0].category == "EMAIL"
        span = res[0].sensitive_spans[0]
        assert span.start == 12 and span.end == 35

    def test_negative(self):
        det = EmailDetector()
        assert det.detect("нет email здесь") == []


class TestPhone:
    def test_positive(self):
        det = PhoneDetector()
        res = det.detect("телефон +7 912 345-67-89")
        assert len(res) == 1
        assert res[0].category == "PHONE"

    def test_positive_8(self):
        det = PhoneDetector()
        res = det.detect("номер 8 (495) 123-45-67")
        assert len(res) == 1

    def test_negative(self):
        det = PhoneDetector()
        assert det.detect("номер заказа 12345") == []

    def test_extension_included(self):
        """A phone extension ('доб. 321') is part of the sensitive span
        (review issue 3D)."""
        det = PhoneDetector()
        res = det.detect("Телефон клиента: +7 (495) 123-45-67 доб. 321")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("Телефон клиента: +7 (495) 123-45-67 доб. 321", span) == "+7 (495) 123-45-67 доб. 321"


class TestCard:
    def test_positive_valid_luhn(self):
        det = CardDetector()
        res = det.detect("карта 4276189074144957")
        assert len(res) == 1
        assert res[0].category == "CARD"
        assert any(s.name == "luhn_valid" for s in res[0].signals)

    def test_positive_grouped(self):
        det = CardDetector()
        res = det.detect("карта 4276 1890 7414 4957")
        assert len(res) == 1

    def test_positive_invalid_luhn_with_context(self):
        # Invalid Luhn but explicit card context -> still masked (R14).
        det = CardDetector()
        res = det.detect("номер карты: 1234567890123456")
        assert len(res) == 1
        assert any(s.name == "luhn_invalid" for s in res[0].signals)

    def test_negative_short_number(self):
        det = CardDetector()
        assert det.detect("код 1234") == []


class TestInn:
    def test_positive_valid_12(self):
        det = InnDetector()
        res = det.detect("ИНН 119098818444")
        assert len(res) == 1
        assert res[0].category == "INN"

    def test_positive_valid_10(self):
        det = InnDetector()
        res = det.detect("ИНН 4072864430")
        assert len(res) == 1

    def test_positive_invalid_checksum_with_context(self):
        det = InnDetector()
        res = det.detect("ИНН клиента: 123456789012")
        assert len(res) == 1

    def test_negative_short(self):
        det = InnDetector()
        assert det.detect("номер 12345") == []


class TestPassport:
    def test_positive_series_number(self):
        det = PassportDetector()
        res = det.detect("паспорт серия 0318 номер 123456")
        assert len(res) == 1
        spans = res[0].sensitive_spans
        assert len(spans) == 2
        # Only the digits are sensitive, not "паспорт/серия/номер".
        assert spans[0].start == 14 and spans[0].end == 18
        assert spans[1].start == 25 and spans[1].end == 31

    def test_evidence_differs_from_sensitive(self):
        det = PassportDetector()
        res = det.detect("паспорт серия 0318 номер 123456")
        evidence = res[0].evidence_spans[0]
        sensitive = res[0].sensitive_spans
        # Evidence covers the whole construction; sensitive only the digits.
        assert evidence.start < sensitive[0].start
        assert evidence.end >= sensitive[-1].end
        # Evidence is strictly wider than the union of sensitive spans.
        assert evidence.length > sum(s.length for s in sensitive)

    def test_negative_no_context(self):
        # A bare 4+6 digit pattern is now detected as a passport series+number
        # (independent review passport_raw case). A single 4-digit or 6-digit
        # number alone is NOT a passport.
        det = PassportDetector()
        assert det.detect("0318") == []
        assert det.detect("123456") == []


class TestDepartmentCode:
    def test_positive(self):
        det = DepartmentCodeDetector()
        res = det.detect("код подразделения 770-123")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert span.start == 18 and span.end == 25

    def test_negative(self):
        det = DepartmentCodeDetector()
        assert det.detect("770-123") == []


class TestBirthDate:
    def test_positive_numeric(self):
        det = BirthDateDetector()
        res = det.detect("дата рождения 12.04.1990")
        assert len(res) == 1
        assert res[0].category == "BIRTH_DATE"

    def test_positive_words(self):
        det = BirthDateDetector()
        res = det.detect("родился 12 апреля 1990 года")
        assert len(res) == 1

    def test_positive_short_context(self):
        det = BirthDateDetector()
        res = det.detect("д.р. 12.04.1990")
        assert len(res) == 1

    def test_negative_no_context(self):
        det = BirthDateDetector()
        assert det.detect("срок действия 12.04.1990") == []


class TestFullName:
    def test_positive_full(self):
        det = FullNameDetector()
        res = det.detect("Клиент Иван Иванович Петров предоставил паспорт")
        assert len(res) == 1
        assert res[0].category == "FULL_NAME"

    def test_positive_initials(self):
        det = FullNameDetector()
        res = det.detect("Клиент Петров И. И.")
        assert len(res) == 1

    def test_negative_historical(self):
        # Historical context: "поэт" and "родился" are not personal-context markers.
        det = FullNameDetector()
        res = det.detect("Поэт Александр Сергеевич Пушкин родился в Москве")
        assert res == []

    def test_negative_plain_words(self):
        det = FullNameDetector()
        assert det.detect("Сегодня хорошая погода в городе") == []


class TestAddress:
    def test_positive(self):
        det = AddressDetector()
        res = det.detect("Адрес клиента: г. Москва, ул. Тверская, д. 15, кв. 42")
        assert len(res) == 1
        assert res[0].category == "ADDRESS"
        # Multiple sensitive components.
        assert len(res[0].sensitive_spans) >= 4

    def test_negative_office(self):
        # Office address without personal context marker.
        det = AddressDetector()
        assert det.detect("Адрес отделения: г. Москва, ул. Тверская, д. 10") == []

    @pytest.mark.parametrize("identifier", [
        "qze8c717369d42x24",
        "меткаЖ717369ю42",
    ])
    def test_postcode_does_not_match_inside_unicode_identifier(self, identifier):
        text = (
            "Адрес клиента: Россия, 350000, г. Краснодар, ул. Северная, д. 15, кв. 8.\n"
            f"Контрольная метка: {identifier}."
        )
        result = AddressDetector().detect(text)
        values = [text[span.start:span.end] for span in result[0].sensitive_spans]
        assert values == ["Россия", "350000", "Краснодар", "Северная", "15", "8"]

    def test_postcode_accepts_punctuation_and_nonbreaking_space_boundaries(self):
        text = "Адрес клиента: Россия,\u00a0350000; г. Краснодар, ул. Северная, д. 15, кв. 8."
        result = AddressDetector().detect(text)
        values = [text[span.start:span.end] for span in result[0].sensitive_spans]
        assert "350000" in values


from app.detectors.card_security import CardholderNameDetector, CvvDetector, PinDetector
from app.detectors.documents import (
    DriverLicenseDetector,
    PassportIssueDateDetector,
    PassportIssuerDetector,
)
from app.detectors.identity import BirthPlaceDetector, CitizenshipDetector


class TestBirthPlace:
    def test_positive_city(self):
        det = BirthPlaceDetector()
        res = det.detect("место рождения: г. Москва")
        assert len(res) == 1
        assert res[0].category == "BIRTH_PLACE"
        span = res[0].sensitive_spans[0]
        assert text_of("место рождения: г. Москва", span) == "Москва"

    def test_positive_rodilsya(self):
        det = BirthPlaceDetector()
        res = det.detect("родился в Москве")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("родился в Москве", span) == "Москве"

    def test_positive_compound(self):
        det = BirthPlaceDetector()
        res = det.detect("место рождения: Нижний Новгород")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("место рождения: Нижний Новгород", span) == "Нижний Новгород"

    def test_evidence_differs_from_sensitive(self):
        det = BirthPlaceDetector()
        res = det.detect("место рождения: г. Москва")
        evidence = res[0].evidence_spans[0]
        sensitive = res[0].sensitive_spans[0]
        assert evidence.start < sensitive.start
        assert evidence.end >= sensitive.end

    def test_negative_ordinary_travel(self):
        det = BirthPlaceDetector()
        assert det.detect("поездка в Германию") == []


class TestCitizenship:
    def test_positive(self):
        det = CitizenshipDetector()
        res = det.detect("гражданство: Российская Федерация")
        assert len(res) == 1
        assert res[0].category == "CITIZENSHIP"
        span = res[0].sensitive_spans[0]
        assert text_of("гражданство: Российская Федерация", span) == "Российская Федерация"

    def test_positive_grajdanin(self):
        det = CitizenshipDetector()
        res = det.detect("гражданин России")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("гражданин России", span) == "России"

    def test_negative_ordinary_country(self):
        det = CitizenshipDetector()
        assert det.detect("поездка в Германию") == []


class TestPassportIssuer:
    def test_positive(self):
        det = PassportIssuerDetector()
        res = det.detect("паспорт выдан ОВД района")
        assert len(res) == 1
        assert res[0].category == "PASSPORT_ISSUER"
        span = res[0].sensitive_spans[0]
        assert text_of("паспорт выдан ОВД района", span) == "ОВД района"

    def test_positive_ufms(self):
        det = PassportIssuerDetector()
        res = det.detect("кем выдан: УФМС России по г. Москве")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("кем выдан: УФМС России по г. Москве", span).startswith("УФМС")

    def test_negative_organization_not_issuer(self):
        det = PassportIssuerDetector()
        assert det.detect("выдан ООО Ромашка") == []

    def test_negative_no_issuer_keyword(self):
        det = PassportIssuerDetector()
        assert det.detect("выдан документ") == []


class TestPassportIssueDate:
    def test_positive_numeric(self):
        det = PassportIssueDateDetector()
        res = det.detect("паспорт выдан 15.03.2015")
        assert len(res) == 1
        assert res[0].category == "PASSPORT_ISSUE_DATE"
        span = res[0].sensitive_spans[0]
        assert text_of("паспорт выдан 15.03.2015", span) == "15.03.2015"

    def test_positive_words(self):
        det = PassportIssueDateDetector()
        res = det.detect("дата выдачи 15 марта 2015 года")
        assert len(res) == 1

    def test_disambiguate_from_birth(self):
        # A date near "родился" is BIRTH_DATE, not issue date.
        det = PassportIssueDateDetector()
        res = det.detect("родился 12.04.1990, паспорт выдан 15.03.2015")
        # Only the issue date should be detected here.
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("родился 12.04.1990, паспорт выдан 15.03.2015", span) == "15.03.2015"

    def test_negative_ordinary_date(self):
        det = PassportIssueDateDetector()
        assert det.detect("встреча 15 мая") == []


class TestDriverLicense:
    def test_positive(self):
        det = DriverLicenseDetector()
        res = det.detect("водительское удостоверение 77 12 345678")
        assert len(res) == 1
        assert res[0].category == "DRIVER_LICENSE"
        span = res[0].sensitive_spans[0]
        assert text_of("водительское удостоверение 77 12 345678", span) == "77 12 345678"

    def test_positive_short_context(self):
        det = DriverLicenseDetector()
        res = det.detect("ВУ 7712 345678")
        assert len(res) == 1

    def test_negative_no_context(self):
        det = DriverLicenseDetector()
        assert det.detect("77 12 345678") == []


class TestCvv:
    def test_positive(self):
        det = CvvDetector()
        res = det.detect("CVV 123")
        assert len(res) == 1
        assert res[0].category == "CVV"
        span = res[0].sensitive_spans[0]
        assert text_of("CVV 123", span) == "123"

    def test_positive_russian(self):
        det = CvvDetector()
        res = det.detect("код безопасности 456")
        assert len(res) == 1

    def test_negative_short_number_no_context(self):
        det = CvvDetector()
        assert det.detect("порт 8080") == []

    def test_negative_year(self):
        det = CvvDetector()
        assert det.detect("год 2024") == []


class TestPin:
    def test_positive(self):
        det = PinDetector()
        res = det.detect("PIN: 4321")
        assert len(res) == 1
        assert res[0].category == "PIN"
        span = res[0].sensitive_spans[0]
        assert text_of("PIN: 4321", span) == "4321"

    def test_positive_pin_kod(self):
        det = PinDetector()
        res = det.detect("пин-код 1234")
        assert len(res) == 1

    def test_positive_cyrillic(self):
        det = PinDetector()
        res = det.detect("ПИН 5678")
        assert len(res) == 1

    def test_negative_no_value(self):
        # "забыл PIN-код" has no value -> not a secret.
        det = PinDetector()
        assert det.detect("забыл PIN-код") == []

    def test_negative_short_number_no_context(self):
        det = PinDetector()
        assert det.detect("код 1234") == []


class TestCardholderName:
    def test_positive_cyrillic(self):
        det = CardholderNameDetector()
        res = det.detect("держатель карты Иван Петров")
        assert len(res) == 1
        assert res[0].category == "CARDHOLDER_NAME"
        span = res[0].sensitive_spans[0]
        assert text_of("держатель карты Иван Петров", span) == "Иван Петров"

    def test_positive_latin(self):
        det = CardholderNameDetector()
        res = det.detect("cardholder JOHN SMITH")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("cardholder JOHN SMITH", span) == "JOHN SMITH"

    def test_positive_imya_na_karte(self):
        det = CardholderNameDetector()
        res = det.detect("имя на карте Анна Иванова")
        assert len(res) == 1

    def test_negative_organization(self):
        det = CardholderNameDetector()
        assert det.detect("держатель ООО Ромашка") == []


def text_of(text, span):
    return text[span.start : span.end]


# ---------------------------------------------------------------------------
# Regression tests for detector bugs found during G3 independent evaluation.
# ---------------------------------------------------------------------------


class TestFullNameRegression:
    def test_context_word_not_in_sensitive_span(self):
        # R13: sensitive span must exclude the service word "Клиент".
        det = FullNameDetector()
        res = det.detect("Клиент Иван Иванович Петров предоставил паспорт")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("Клиент Иван Иванович Петров предоставил паспорт", span) == "Иван Иванович Петров"

    def test_all_uppercase_name(self):
        det = FullNameDetector()
        res = det.detect("клиент ИВАН ИВАНОВИЧ ПЕТРОВ")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("клиент ИВАН ИВАНОВИЧ ПЕТРОВ", span) == "ИВАН ИВАНОВИЧ ПЕТРОВ"

    def test_inflected_name_genitive(self):
        """An inflected (genitive) full name must be detected (review issue 3A)."""
        det = FullNameDetector()
        res = det.detect("Заявление от Кузнецовой Елены Андреевны.")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("Заявление от Кузнецовой Елены Андреевны.", span) == "Кузнецовой Елены Андреевны"

    def test_client_poet_overrides_public_person(self):
        """A client marker overrides the public-person exemption (review issue 3C)."""
        det = FullNameDetector()
        res = det.detect("Клиент, поэт Сергей Николаевич Орлов, оставил заявление.")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("Клиент, поэт Сергей Николаевич Орлов, оставил заявление.", span) == "Сергей Николаевич Орлов"

    def test_pure_public_person_not_masked(self):
        """A pure public-person reference (no client marker) is not masked."""
        det = FullNameDetector()
        res = det.detect("Поэт Александр Сергеевич Пушкин родился в Москве.")
        assert res == []

    def test_cardholder_name_not_claimed_as_full_name(self):
        # A name in cardholder context belongs to CARDHOLDER_NAME, not FULL_NAME.
        det = FullNameDetector()
        res = det.detect("держатель карты Иван Петров")
        assert res == []


class TestBirthDateRegression:
    def test_only_nearest_date_to_birth_context(self):
        # "дата рождения 12.04.1990, паспорт выдан 15.03.2015" -> only 12.04.1990.
        det = BirthDateDetector()
        res = det.detect("дата рождения 12.04.1990, паспорт выдан 15.03.2015")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("дата рождения 12.04.1990, паспорт выдан 15.03.2015", span) == "12.04.1990"

    def test_compound_written_date(self):
        """A compound written date like 'двадцать третьего мая 1987' must be
        fully masked (review issue 3B)."""
        det = BirthDateDetector()
        res = det.detect("Дата рождения: двадцать третьего мая 1987 года")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("Дата рождения: двадцать третьего мая 1987 года", span) == "двадцать третьего мая 1987"

    def test_compound_written_date_thirty_first(self):
        det = BirthDateDetector()
        res = det.detect("Дата рождения: тридцать первого декабря 1990 года")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("Дата рождения: тридцать первого декабря 1990 года", span) == "тридцать первого декабря 1990"


class TestPassportRegression:
    def test_series_with_space(self):
        det = PassportDetector()
        res = det.detect("паспорт серия 45 12 номер 789012")
        assert len(res) == 1
        spans = res[0].sensitive_spans
        assert len(spans) == 2
        assert text_of("паспорт серия 45 12 номер 789012", spans[0]) == "45 12"
        assert text_of("паспорт серия 45 12 номер 789012", spans[1]) == "789012"


class TestPassportIssuerRegression:
    def test_abbreviation_boundary(self):
        # "г." abbreviation must not truncate the issuing authority name.
        det = PassportIssuerDetector()
        res = det.detect("кем выдан: УФМС России по г. Москве")
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of("кем выдан: УФМС России по г. Москве", span) == "УФМС России по г. Москве"


class TestPassportIssueDateRegression:
    def test_ordinary_date_with_neutral_context_not_masked(self):
        # The issue date is masked; the ordinary "встреча" date is not.
        det = PassportIssueDateDetector()
        text = "паспорт выдан 15.03.2015, встреча 20.06.2020"
        res = det.detect(text)
        assert len(res) == 1
        span = res[0].sensitive_spans[0]
        assert text_of(text, span) == "15.03.2015"


class TestAddressRegression:
    def test_office_context_does_not_suppress_later_client_address(self):
        # One "офис" must not make the whole document non-PII (master prompt 9.1).
        det = AddressDetector()
        text = "Адрес отделения: г. Москва, ул. Тверская, д. 10. Адрес клиента: г. Казань, ул. Баумана, д. 7, кв. 3"
        res = det.detect(text)
        assert len(res) == 1
        values = [text[s.start:s.end] for s in res[0].sensitive_spans]
        assert "Казань" in values
        assert "Баумана" in values
        assert "Москва" not in values


class TestBirthPlaceRegression:
    def test_historical_person_birth_place_not_masked(self):
        # "Поэт ... Пушкин родился в Москве" is historical, not a client record.
        det = BirthPlaceDetector()
        assert det.detect("Поэт Александр Сергеевич Пушкин родился в Москве") == []
