import pytest

from app.detectors.card_security import CardholderNameDetector


@pytest.mark.parametrize(('text', 'expected'), [
    ('Держатель договора Ада Лавлейс попросила изменить телефон.', []),
    ('ДЕРЖАТЕЛЬ ПОЛИСА ИВАН ПЕТРОВ', []),
    ('Держатель акций Пётр Иванов пришёл в банк.', []),
    ('держатель карты Ада Лавлейс попросила изменить телефон.', ['Ада Лавлейс']),
    ('держатель карты Иван Петров сообщил данные.', ['Иван Петров']),
    ('Держатель: Иван Петров', ['Иван Петров']),
    ('cardholder JOHN SMITH', ['JOHN SMITH']),
    ('cardholder john smith', ['john smith']),
    ('держатель карты иван петров сообщил телефон.', ['иван петров']),
    ('держатель карты Иван\u00a0\u00a0Петров сообщил телефон.', ['Иван\u00a0\u00a0Петров']),
    ('держатель ООО Ромашка', []),
])
def test_cardholder_scope_and_original_boundaries(text, expected):
    detections = CardholderNameDetector().detect(text)
    assert [text[s.start:s.end] for d in detections for s in d.sensitive_spans] == expected
