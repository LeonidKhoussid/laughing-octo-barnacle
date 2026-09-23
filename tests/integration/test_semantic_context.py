"""Context regressions using the installed models and the default pipeline."""
import pytest

from app.core.engine import Engine
from app.detectors.registry import DetectorRegistry


@pytest.fixture(scope="module")
def engine():
    return Engine(DetectorRegistry.from_config("configs/detectors.yaml").detectors)


@pytest.mark.parametrize("text, private, public", [
    ("Заявление от Кузнецовой Елены Андреевны.", ["Кузнецовой Елены Андреевны"], ["Заявление от"]),
    ("Лаборант Юлиан Викторович Зотов готовит доклад об опытах Грегора Менделя.",
     ["Юлиан Викторович Зотов"], ["Грегора Менделя"]),
    ("Держатель договора Ада Лавлейс попросила изменить номер телефона на +7 921 684-37-52.",
     ["Ада Лавлейс", "+7 921 684-37-52"], ["Держатель договора", "попросила изменить"]),
    ("В докладе об алгоритмах Эдсгера Дейкстры организатор указал личный телефон участника Георгия Фёдоровича Рубцова: +7 903 624-71-58.",
     ["Георгия Фёдоровича Рубцова", "+7 903 624-71-58"], ["Эдсгера Дейкстры"]),
    ("Драматург Бернард Шоу написал «Пигмалион». Мой знакомый Бернард Шоу прислал личное сообщение.",
     [], ["Драматург Бернард Шоу написал"]),
])
def test_person_specific_context_and_exact_restoration(engine, text, private, public):
    result = engine.mask(text, "context-regression", "example")
    for value in private:
        assert value not in result.masked_text
    for value in public:
        assert value in result.masked_text
    if "Мой знакомый" in text:
        assert "Мой знакомый Бернард Шоу" not in result.masked_text
    assert engine.unmask(result.masked_text, result.token_mapping) == text
