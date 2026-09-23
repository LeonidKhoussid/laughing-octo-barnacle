"""Independent boundary regressions; public/private adjudication is elsewhere."""
import pytest

from app.detectors.dates import BirthDateDetector
from app.detectors.identity import BirthPlaceDetector
from app.detectors.structured import EmailDetector


def values(detector, text):
    return [text[span.start:span.end] for item in detector.detect(text) for span in item.sensitive_spans]


@pytest.mark.parametrize(('text', 'expected'), [
    ('Личный email: nina@example.test.', ['nina@example.test']),
    ('Напишите: a.b+news@sub.example.org. Затем позвоните.', ['a.b+news@sub.example.org']),
    ('Контакт (NINA@EXAMPLE.COM), второй — olga@example.net!', ['NINA@EXAMPLE.COM', 'olga@example.net']),
    ('Адрес user@example.test: отправьте заявку.', ['user@example.test']),
    ('Адрес user@example.c', []),
    ('Ошибка user@example.test123', []),
])
def test_email_punctuation_and_complete_domain(text, expected):
    assert values(EmailDetector(), text) == expected


@pytest.mark.parametrize(('text', 'expected'), [
    ('Клиент указал дату рождения 17.09.1993.', ['17.09.1993']),
    ('Ошибка в дате рождения: 2001-03-24.', ['2001-03-24']),
    ('Уточнение даты рождения: 24 марта 2001 года.', ['24 марта 2001']),
    ('Дата рождения клиента Всеволод Анатольевич Архангельский: 24.03.2001.', ['24.03.2001']),
    ('Дата рождения клиента В. А. Архангельский: 24.03.2001.', ['24.03.2001']),
    ('Конференция состоится 12.04.2026. Дата рождения клиента Всеволод Архангельский: 24.03.2001.', ['24.03.2001']),
    ('Дата рождения не указана. Встреча состоится 24.03.2026.', []),
    ('Дата рождения: неизвестна; паспорт выдан 24.03.2020.', []),
    ('24.03.2001 — дата рождения; 25.03.2021 — паспорт выдан.', ['24.03.2001']),
    ('Поэт Александр Сергеевич Пушкин родился 12 апреля 1990 года.', []),
    ('Клиент Александр Сергеевич Пушкин родился 12 апреля 1990 года.', ['12 апреля 1990']),
])
def test_birth_date_attaches_to_its_clause(text, expected):
    assert values(BirthDateDetector(), text) == expected


@pytest.mark.parametrize(('text', 'expected'), [
    ('Клиентка родилась 24 марта 2001 года в Перми.', ['Перми']),
    ('Клиент родился 2001-03-24 в Омске.', ['Омске']),
    ('Клиент родился двадцать четвёртого марта 2001 года в Нижнем Новгороде.', ['Нижнем Новгороде']),
    ('Клиент родился в Твери и переехал в Москву.', ['Твери']),
    ('Место рождения: г.Киров.', ['Киров']),
    ('Место рождения: москва.', ['москва']),
    ('Место рождения: г. Великий Новгород.', ['Великий Новгород']),
    ('Поэт Александр Сергеевич Пушкин родился 12 апреля 1990 года в Москве.', []),
    ('Клиент Александр Сергеевич Пушкин родился 12 апреля 1990 года в Москве.', ['Москве']),
    ('Поэт Пушкин родился в Москве. Клиент родился 24 марта 2001 года в Перми.', ['Перми']),
    ('Клиент родился. Встреча будет в Москве.', []),
])
def test_birth_place_after_date_preserves_subject_and_value_boundaries(text, expected):
    assert values(BirthPlaceDetector(), text) == expected
