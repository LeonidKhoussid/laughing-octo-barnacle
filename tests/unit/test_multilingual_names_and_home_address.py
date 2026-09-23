"""Focused regressions for explicit name fields and private address components."""

import pytest

from app.detectors.address import AddressDetector
from app.detectors.person import FullNameDetector


def values(detector, text):
    return [text[span.start:span.end] for detection in detector.detect(text)
            for span in detection.sensitive_spans]


@pytest.mark.parametrize(("text", "expected"), [
    ("Закрытая карточка: полное имя клиента Zoë Márquez, документ проверен.", "Zoë Márquez"),
    ("Имя получателя записано латиницей: Alyona Romanovna Sviridova.", "Alyona Romanovna Sviridova"),
    ("Клиент Çınar Şentürk просит вернуть платёж.", "Çınar Şentürk"),
    ("Клиент Άννα Παπαδοπούλου просит вернуть платёж.", "Άννα Παπαδοπούλου"),
    ("Клиент Anne‑Marie O’Neill просит вернуть платёж.", "Anne‑Marie O’Neill"),
    ("📋 ФИО: Zoe\u0308 Ma\u0301rquez, документ проверен.", "Zoe\u0308 Ma\u0301rquez"),
    ("КЛИЕНТ ÉLODIE DURAND, телефон указан ниже.", "ÉLODIE DURAND"),
])
def test_explicit_person_role_supports_unfamiliar_unicode_name(text, expected):
    assert values(FullNameDetector(), text) == [expected]


@pytest.mark.parametrize(("text", "expected"), [
    ("В закрытом списке доставки сборника «Тихая пристань» получатель — Ида Романовна Лунская; экземпляр оплачен лично.", "Ида Романовна Лунская"),
    ("Заказчик Есения Мироновна Яровая оплатил заказ.", "Есения Мироновна Яровая"),
    ("Заказ от получателя Есении Мироновны Яровой оплачен.", "Есении Мироновны Яровой"),
    ("Получательница — Élodie Durand, подпись проверена.", "Élodie Durand"),
    ("Полное имя заказчицы указано латиницей: Anne‑Marie O’Neill.", "Anne‑Marie O’Neill"),
])
def test_explicit_recipient_and_customer_roles_support_unfamiliar_names(text, expected):
    assert values(FullNameDetector(), text) == [expected]


@pytest.mark.parametrize("text", [
    "Обсуждаем Zoë Márquez.",
    "Клиент использует Microsoft Office.",
    "Клиент заказал Black Coffee.",
    "Клиент ожидает ответа. Zoë Márquez указана на афише.",
    "Имя проекта: Blue Planet.",
    "Клиент смотрит фильм «Anne‑Marie O’Neill».",
    "Получатель использует Microsoft Office.",
    "Заказчик заказал Новый Альбом.",
    "Для получения Blue Planet требуется билет.",
    "Получатель — компания «Blue Planet».",
    "Заказчик ожидает ответа. Élodie Durand указана на афише.",
])
def test_capitalization_without_attached_person_role_is_insufficient(text):
    assert values(FullNameDetector(), text) == []


@pytest.mark.parametrize("street_label", ["переулок", "пер."])
def test_home_pickup_masks_private_address_components(street_label):
    text = f"Личная заявка на такси из дома: г. Берёзовск, {street_label} Тихий, д. 9."
    assert values(AddressDetector(), text) == ["Берёзовск", "Тихий", "9"]


def test_home_marker_overrides_preceding_office_address():
    text = "Адрес офиса: г. Москва, ул. Тверская, д. 10. Такси из дома: г. Омск, пер. Южный, д. 2."
    assert values(AddressDetector(), text) == ["Омск", "Южный", "2"]


def test_meeting_location_without_private_context_remains_ambiguous():
    assert values(AddressDetector(), "Место встречи: ул. Сосновая, д. 8.") == []


def test_public_office_alley_is_not_private_address():
    assert values(AddressDetector(), "Адрес офиса: г. Омск, пер. Южный, д. 2.") == []
