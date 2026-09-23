"""Independent challenge set across all 17 categories (Layer B).

Expected spans are defined BEFORE detector execution, from templates, NOT from
detector predictions. Includes positive cases, hard negatives, writing
variations, and mixed documents. This is a separate evaluation from the old
47-case regression set (which is now known regression input).
"""
from __future__ import annotations

import json
import random
from pathlib import Path

SEED = 20260922
ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
OUT = ARTIFACTS / "challenge_set.json"


def _span(text: str, fragment: str) -> dict:
    start = text.index(fragment)
    return {"start": start, "end": start + len(fragment), "category": "?"}


def build() -> list[dict]:
    """Return a list of {text, gold_spans, category, scenario} examples.

    Gold spans are computed from the literal fragment positions in the template
    text (independent of detector predictions).
    """
    examples: list[dict] = []

    def add(text, fragments, category, scenario):
        gold = []
        for frag in fragments:
            start = text.index(frag)
            gold.append({"start": start, "end": start + len(frag), "category": category})
        examples.append({"text": text, "gold_spans": gold, "category": category, "scenario": scenario})

    # FULL_NAME
    add("Клиент: Сидоров А. В.", ["Сидоров А. В."], "FULL_NAME", "initials")
    add("Заявление от Кузнецовой Елены Андреевны.", ["Кузнецовой Елены Андреевны"], "FULL_NAME", "genitive")
    add("Клиент, поэт Сергей Николаевич Орлов, оставил заявление.", ["Сергей Николаевич Орлов"], "FULL_NAME", "client_poet")
    add("Клиент ИВАНОВ ИВАН ИВАНОВИЧ", ["ИВАНОВ ИВАН ИВАНОВИЧ"], "FULL_NAME", "uppercase")
    add("Поэт Александр Сергеевич Пушкин родился в Москве.", [], "FULL_NAME", "public_person_negative")

    # BIRTH_DATE
    add("Дата рождения: двадцать третьего мая 1987 года", ["двадцать третьего мая 1987"], "BIRTH_DATE", "compound_written")
    add("Дата рождения: 12 апреля 1990 года", ["12 апреля 1990"], "BIRTH_DATE", "words")
    add("Дата рождения: 1990-04-12", ["1990-04-12"], "BIRTH_DATE", "iso")
    add("Дата рождения: 04/25/1990", ["04/25/1990"], "BIRTH_DATE", "us")
    add("Встреча назначена на 15 мая 2024 года.", [], "BIRTH_DATE", "ordinary_date_negative")

    # BIRTH_PLACE
    add("Место рождения: г. Москва", ["Москва"], "BIRTH_PLACE", "city")
    add("Клиент работает архитектором. Место рождения: Москва.", ["Москва"], "BIRTH_PLACE", "client_profession")

    # PASSPORT
    add("Паспорт серия 0318 номер 123456", ["0318", "123456"], "PASSPORT", "series_number")
    add("Паспорт серия 50 12 номер 654321", ["50 12", "654321"], "PASSPORT", "split_series")
    add("Паспорт: 4503 123456", ["4503", "123456"], "PASSPORT", "bare_label")

    # CITIZENSHIP
    add("Гражданство: Российская Федерация", ["Российская Федерация"], "CITIZENSHIP", "country")
    add("Поездка в Германию запланирована на лето.", [], "CITIZENSHIP", "ordinary_country_negative")

    # PASSPORT_ISSUER
    add("Паспорт выдан ОМВД России по району Тверской", ["ОМВД России по району Тверской"], "PASSPORT_ISSUER", "issuer")
    add("Паспорт выдан омвд россии по району тверской", ["омвд россии по району тверской"], "PASSPORT_ISSUER", "issuer_lowercase")

    # DEPARTMENT_CODE
    add("Код подразделения 770-001", ["770-001"], "DEPARTMENT_CODE", "code")
    add("Код подразделения 770–001", ["770–001"], "DEPARTMENT_CODE", "unicode_dash")

    # PASSPORT_ISSUE_DATE
    add("Дата выдачи паспорта: 20.07.2010", ["20.07.2010"], "PASSPORT_ISSUE_DATE", "numeric")
    add("Дата выдачи паспорта: 2010-07-20", ["2010-07-20"], "PASSPORT_ISSUE_DATE", "iso")

    # DRIVER_LICENSE
    add("Водительское удостоверение 77 12 345678", ["77 12 345678"], "DRIVER_LICENSE", "license")

    # ADDRESS
    add("Адрес клиента: г. Ростов-на-Дону, ул. Большая Садовая, д. 27, кв. 8.",
        ["Ростов-на-Дону", "Большая Садовая", "27", "8"], "ADDRESS", "multiword")
    add("Адрес клиента: г.Москва, ул.Тверская, д.15, кв.42.",
        ["Москва", "Тверская", "15", "42"], "ADDRESS", "compact")
    add("Отделение банка расположено по адресу: г. Москва, ул. Тверская, д. 15.", [], "ADDRESS", "office_negative")

    # EMAIL
    add("Email: ivan.petrov@example.com", ["ivan.petrov@example.com"], "EMAIL", "email")
    add("Свяжитесь с нами по адресу info@bank.ru", ["info@bank.ru"], "EMAIL", "email2")

    # PHONE
    add("Телефон клиента: +7 (495) 123-45-67 доб. 321", ["+7 (495) 123-45-67 доб. 321"], "PHONE", "extension")
    add("Телефон: +7 912 345-67-89", ["+7 912 345-67-89"], "PHONE", "phone")
    add("Номер заказа 12345", [], "PHONE", "order_negative")

    # INN
    add("ИНН 7707083893", ["7707083893"], "INN", "inn10")
    add("ИНН 123456789012", ["123456789012"], "INN", "inn12")
    add("Номер заказа 123456789012", [], "INN", "order_negative")

    # CARD
    add("Карта 4276189074144957", ["4276189074144957"], "CARD", "card")
    add("Карта 4276 1234 5678 9012", ["4276 1234 5678 9012"], "CARD", "card_spaced")
    add("Номер заказа 1234567890123456", [], "CARD", "order_negative")

    # CVV
    add("CVV-код: 123", ["123"], "CVV", "cvv")
    add("Код безопасности 456", ["456"], "CVV", "cvv2")

    # PIN
    add("ПИН-код карты: 1234", ["1234"], "PIN", "pin")
    add("PIN: 4321", ["4321"], "PIN", "pin2")
    add("Я забыл PIN-код", [], "PIN", "no_value_negative")

    # CARDHOLDER_NAME
    add("Держатель карты Иван Петров", ["Иван Петров"], "CARDHOLDER_NAME", "holder")
    add("Имя на карте: IVAN IVANOV", ["IVAN IVANOV"], "CARDHOLDER_NAME", "latin")

    # Mixed document (specific categories per fragment).
    mixed_text = (
        "Клиент Иванов Иван Петрович, дата рождения 12 апреля 1990 года, "
        "паспорт серия 0318 номер 123456, email ivan.petrov@example.com, "
        "телефон +7 912 345-67-89, карта 4276189074144957."
    )
    mixed_gold = [
        {"start": mixed_text.index("Иванов Иван Петрович"), "end": mixed_text.index("Иванов Иван Петрович") + len("Иванов Иван Петрович"), "category": "FULL_NAME"},
        {"start": mixed_text.index("12 апреля 1990"), "end": mixed_text.index("12 апреля 1990") + len("12 апреля 1990"), "category": "BIRTH_DATE"},
        {"start": mixed_text.index("0318"), "end": mixed_text.index("0318") + len("0318"), "category": "PASSPORT"},
        {"start": mixed_text.index("123456"), "end": mixed_text.index("123456") + len("123456"), "category": "PASSPORT"},
        {"start": mixed_text.index("ivan.petrov@example.com"), "end": mixed_text.index("ivan.petrov@example.com") + len("ivan.petrov@example.com"), "category": "EMAIL"},
        {"start": mixed_text.index("+7 912 345-67-89"), "end": mixed_text.index("+7 912 345-67-89") + len("+7 912 345-67-89"), "category": "PHONE"},
        {"start": mixed_text.index("4276189074144957"), "end": mixed_text.index("4276189074144957") + len("4276189074144957"), "category": "CARD"},
    ]
    examples.append({"text": mixed_text, "gold_spans": mixed_gold, "category": "MIXED", "scenario": "mixed_document"})

    return examples


def write(examples: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "seed": SEED,
        "generator": "scripts/generate_challenge.py",
        "note": "Independent challenge set. Gold spans defined before detector execution, not from predictions.",
        "examples": examples,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> int:
    examples = build()
    write(examples, OUT)
    cats = {}
    for ex in examples:
        cats.setdefault(ex["category"], 0)
        cats[ex["category"]] += 1
    print(f"Wrote {len(examples)} examples to {OUT}")
    print("Per-category counts:")
    for cat in sorted(cats):
        print(f"  {cat}: {cats[cat]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())