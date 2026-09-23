"""Generate an independent labeled synthetic fixture set for all 17 PII categories.

The generator produces ground truth from TEMPLATES, not from detector predictions.
Each example carries:
  - text: the synthetic input string
  - gold_spans: list of {start, end, category} in Unicode code-point coords
  - category: the primary category under test
  - scenario: a short label (positive/negative/case_variant/nonspace/overlap/
              bare_value/typo_checksum/historical_person/office_vs_residential)

A fixed seed makes the output reproducible. All values are synthetic; no real PII.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
OUT = ARTIFACTS / "fixtures.json"

SEED = 20260922


def span(text: str, needle: str, category: str, start_offset: int = 0) -> dict:
    """Return a gold span dict for the first occurrence of needle in text."""
    idx = text.find(needle, start_offset)
    if idx < 0:
        raise ValueError(f"needle {needle!r} not found in {text!r}")
    return {"start": idx, "end": idx + len(needle), "category": category}


def spans(text: str, needles: list[tuple[str, str]], start_offset: int = 0) -> list[dict]:
    """Return gold spans for a list of (needle, category) pairs."""
    out = []
    cursor = start_offset
    for needle, category in needles:
        out.append(span(text, needle, category, cursor))
        cursor = out[-1]["end"]
    return out


def build() -> list[dict]:
    rng = random.Random(SEED)
    examples: list[dict] = []

    def add(text, gold_spans, category, scenario):
        examples.append(
            {
                "text": text,
                "gold_spans": gold_spans,
                "category": category,
                "scenario": scenario,
            }
        )

    # ------------------------------------------------------------------ EMAIL
    add("свяжитесь с ivan.petrov@example.com пожалуйста",
        [span("свяжитесь с ivan.petrov@example.com пожалуйста", "ivan.petrov@example.com", "EMAIL")],
        "EMAIL", "positive")
    add("почта: ANNA.SMIRNOVA@TEST.ORG",
        [span("почта: ANNA.SMIRNOVA@TEST.ORG", "ANNA.SMIRNOVA@TEST.ORG", "EMAIL")],
        "EMAIL", "case_variant")
    add("email a.b-c_d@sub.example.co.uk",
        [span("email a.b-c_d@sub.example.co.uk", "a.b-c_d@sub.example.co.uk", "EMAIL")],
        "EMAIL", "positive")
    add("контакт: user+tag@example.com",
        [span("контакт: user+tag@example.com", "user+tag@example.com", "EMAIL")],
        "EMAIL", "positive")
    add("нет email в этом тексте", [], "EMAIL", "negative")

    # ------------------------------------------------------------------ PHONE
    add("телефон +7 912 345-67-89",
        [span("телефон +7 912 345-67-89", "+7 912 345-67-89", "PHONE")],
        "PHONE", "positive")
    add("номер 8 (495) 123-45-67",
        [span("номер 8 (495) 123-45-67", "8 (495) 123-45-67", "PHONE")],
        "PHONE", "positive")
    add("тел +79123456789",
        [span("тел +79123456789", "+79123456789", "PHONE")],
        "PHONE", "nonspace")
    add("моб. 7 912 345 67 89",
        [span("моб. 7 912 345 67 89", "7 912 345 67 89", "PHONE")],
        "PHONE", "nonspace")
    add("номер заказа 12345", [], "PHONE", "negative")

    # ------------------------------------------------------------------ CARD
    add("карта 4276189074144957",
        [span("карта 4276189074144957", "4276189074144957", "CARD")],
        "CARD", "positive")
    add("карта 4276 1890 7414 4957",
        [span("карта 4276 1890 7414 4957", "4276 1890 7414 4957", "CARD")],
        "CARD", "nonspace")
    add("номер карты: 1234567890123456",
        [span("номер карты: 1234567890123456", "1234567890123456", "CARD")],
        "CARD", "typo_checksum")
    add("карта 5555-3618-8073-8251",
        [span("карта 5555-3618-8073-8251", "5555-3618-8073-8251", "CARD")],
        "CARD", "nonspace")
    add("код 1234", [], "CARD", "negative")

    # ------------------------------------------------------------------ INN
    add("ИНН 119098818444",
        [span("ИНН 119098818444", "119098818444", "INN")],
        "INN", "positive")
    add("ИНН 4072864430",
        [span("ИНН 4072864430", "4072864430", "INN")],
        "INN", "positive")
    add("ИНН клиента: 123456789012",
        [span("ИНН клиента: 123456789012", "123456789012", "INN")],
        "INN", "typo_checksum")
    add("номер 12345", [], "INN", "negative")

    # ------------------------------------------------------------------ PASSPORT
    add("паспорт серия 0318 номер 123456",
        [span("паспорт серия 0318 номер 123456", "0318", "PASSPORT"),
         span("паспорт серия 0318 номер 123456", "123456", "PASSPORT")],
        "PASSPORT", "positive")
    add("паспорт серия 45 12 номер 789012",
        [span("паспорт серия 45 12 номер 789012", "45 12", "PASSPORT"),
         span("паспорт серия 45 12 номер 789012", "789012", "PASSPORT")],
        "PASSPORT", "nonspace")
    add("паспорт серия 0318, номер 123456",
        [span("паспорт серия 0318, номер 123456", "0318", "PASSPORT"),
         span("паспорт серия 0318, номер 123456", "123456", "PASSPORT")],
        "PASSPORT", "positive")
    add("0318 123456", [], "PASSPORT", "negative")

    # ------------------------------------------------------------------ DEPARTMENT_CODE
    add("код подразделения 770-123",
        [span("код подразделения 770-123", "770-123", "DEPARTMENT_CODE")],
        "DEPARTMENT_CODE", "positive")
    add("код подразделения 770 123",
        [span("код подразделения 770 123", "770 123", "DEPARTMENT_CODE")],
        "DEPARTMENT_CODE", "nonspace")
    add("770-123", [], "DEPARTMENT_CODE", "negative")

    # ------------------------------------------------------------------ FULL_NAME
    add("Клиент Иван Иванович Петров предоставил паспорт",
        [span("Клиент Иван Иванович Петров предоставил паспорт", "Иван Иванович Петров", "FULL_NAME")],
        "FULL_NAME", "positive")
    add("Клиент Петров И. И.",
        [span("Клиент Петров И. И.", "Петров И. И.", "FULL_NAME")],
        "FULL_NAME", "positive")
    add("клиент ИВАН ИВАНОВИЧ ПЕТРОВ",
        [span("клиент ИВАН ИВАНОВИЧ ПЕТРОВ", "ИВАН ИВАНОВИЧ ПЕТРОВ", "FULL_NAME")],
        "FULL_NAME", "case_variant")
    add("Заявитель Анна Сергеевна Иванова",
        [span("Заявитель Анна Сергеевна Иванова", "Анна Сергеевна Иванова", "FULL_NAME")],
        "FULL_NAME", "positive")
    # Historical person in non-client context -> NOT masked.
    add("Поэт Александр Сергеевич Пушкин родился в Москве", [],
        "FULL_NAME", "historical_person")
    # Client namesake -> masked.
    add("Клиент Александр Сергеевич Пушкин предоставил паспорт",
        [span("Клиент Александр Сергеевич Пушкин предоставил паспорт", "Александр Сергеевич Пушкин", "FULL_NAME")],
        "FULL_NAME", "historical_person")
    add("Сегодня хорошая погода в городе", [], "FULL_NAME", "negative")

    # ------------------------------------------------------------------ BIRTH_DATE
    add("дата рождения 12.04.1990",
        [span("дата рождения 12.04.1990", "12.04.1990", "BIRTH_DATE")],
        "BIRTH_DATE", "positive")
    add("родился 12 апреля 1990 года",
        [span("родился 12 апреля 1990 года", "12 апреля 1990 года", "BIRTH_DATE")],
        "BIRTH_DATE", "positive")
    add("д.р. 12.04.1990",
        [span("д.р. 12.04.1990", "12.04.1990", "BIRTH_DATE")],
        "BIRTH_DATE", "positive")
    add("дата рождения 12/04/1990",
        [span("дата рождения 12/04/1990", "12/04/1990", "BIRTH_DATE")],
        "BIRTH_DATE", "nonspace")
    add("срок действия 12.04.1990", [], "BIRTH_DATE", "negative")

    # ------------------------------------------------------------------ BIRTH_PLACE
    add("место рождения: г. Москва",
        [span("место рождения: г. Москва", "Москва", "BIRTH_PLACE")],
        "BIRTH_PLACE", "positive")
    add("родился в Москве",
        [span("родился в Москве", "Москве", "BIRTH_PLACE")],
        "BIRTH_PLACE", "positive")
    add("место рождения: Нижний Новгород",
        [span("место рождения: Нижний Новгород", "Нижний Новгород", "BIRTH_PLACE")],
        "BIRTH_PLACE", "positive")
    add("поездка в Германию", [], "BIRTH_PLACE", "negative")

    # ------------------------------------------------------------------ CITIZENSHIP
    add("гражданство: Российская Федерация",
        [span("гражданство: Российская Федерация", "Российская Федерация", "CITIZENSHIP")],
        "CITIZENSHIP", "positive")
    add("гражданин России",
        [span("гражданин России", "России", "CITIZENSHIP")],
        "CITIZENSHIP", "positive")
    add("гражданство: Республика Беларусь",
        [span("гражданство: Республика Беларусь", "Республика Беларусь", "CITIZENSHIP")],
        "CITIZENSHIP", "positive")
    add("поездка в Германию", [], "CITIZENSHIP", "negative")

    # ------------------------------------------------------------------ PASSPORT_ISSUER
    add("паспорт выдан ОВД района",
        [span("паспорт выдан ОВД района", "ОВД района", "PASSPORT_ISSUER")],
        "PASSPORT_ISSUER", "positive")
    add("кем выдан: УФМС России по г. Москве",
        [span("кем выдан: УФМС России по г. Москве", "УФМС России по г. Москве", "PASSPORT_ISSUER")],
        "PASSPORT_ISSUER", "positive")
    add("паспорт выдан ОМВД России по району",
        [span("паспорт выдан ОМВД России по району", "ОМВД России по району", "PASSPORT_ISSUER")],
        "PASSPORT_ISSUER", "positive")
    add("выдан ООО Ромашка", [], "PASSPORT_ISSUER", "negative")

    # ------------------------------------------------------------------ PASSPORT_ISSUE_DATE
    add("паспорт выдан 15.03.2015",
        [span("паспорт выдан 15.03.2015", "15.03.2015", "PASSPORT_ISSUE_DATE")],
        "PASSPORT_ISSUE_DATE", "positive")
    add("дата выдачи 15 марта 2015 года",
        [span("дата выдачи 15 марта 2015 года", "15 марта 2015 года", "PASSPORT_ISSUE_DATE")],
        "PASSPORT_ISSUE_DATE", "positive")
    add("родился 12.04.1990, паспорт выдан 15.03.2015",
        [span("родился 12.04.1990, паспорт выдан 15.03.2015", "12.04.1990", "BIRTH_DATE"),
         span("родился 12.04.1990, паспорт выдан 15.03.2015", "15.03.2015", "PASSPORT_ISSUE_DATE")],
        "PASSPORT_ISSUE_DATE", "overlap")
    add("встреча 15 мая", [], "PASSPORT_ISSUE_DATE", "negative")

    # ------------------------------------------------------------------ DRIVER_LICENSE
    add("водительское удостоверение 77 12 345678",
        [span("водительское удостоверение 77 12 345678", "77 12 345678", "DRIVER_LICENSE")],
        "DRIVER_LICENSE", "positive")
    add("ВУ 7712 345678",
        [span("ВУ 7712 345678", "7712 345678", "DRIVER_LICENSE")],
        "DRIVER_LICENSE", "positive")
    add("водительские права 99 01 123456",
        [span("водительские права 99 01 123456", "99 01 123456", "DRIVER_LICENSE")],
        "DRIVER_LICENSE", "positive")
    add("77 12 345678", [], "DRIVER_LICENSE", "negative")

    # ------------------------------------------------------------------ ADDRESS
    add("Адрес клиента: г. Москва, ул. Тверская, д. 15, кв. 42",
        [span("Адрес клиента: г. Москва, ул. Тверская, д. 15, кв. 42", "Москва", "ADDRESS"),
         span("Адрес клиента: г. Москва, ул. Тверская, д. 15, кв. 42", "Тверская", "ADDRESS"),
         span("Адрес клиента: г. Москва, ул. Тверская, д. 15, кв. 42", "15", "ADDRESS"),
         span("Адрес клиента: г. Москва, ул. Тверская, д. 15, кв. 42", "42", "ADDRESS")],
        "ADDRESS", "positive")
    add("проживает: г. Казань, ул. Баумана, д. 7, кв. 3",
        [span("проживает: г. Казань, ул. Баумана, д. 7, кв. 3", "Казань", "ADDRESS"),
         span("проживает: г. Казань, ул. Баумана, д. 7, кв. 3", "Баумана", "ADDRESS"),
         span("проживает: г. Казань, ул. Баумана, д. 7, кв. 3", "7", "ADDRESS"),
         span("проживает: г. Казань, ул. Баумана, д. 7, кв. 3", "3", "ADDRESS")],
        "ADDRESS", "positive")
    # Office address -> NOT masked.
    add("Адрес отделения: г. Москва, ул. Тверская, д. 10", [],
        "ADDRESS", "office_vs_residential")
    # Office then client address in one text -> only client address masked.
    add("Адрес отделения: г. Москва, ул. Тверская, д. 10. Адрес клиента: г. Казань, ул. Баумана, д. 7, кв. 3",
        [span("Адрес отделения: г. Москва, ул. Тверская, д. 10. Адрес клиента: г. Казань, ул. Баумана, д. 7, кв. 3", "Казань", "ADDRESS"),
         span("Адрес отделения: г. Москва, ул. Тверская, д. 10. Адрес клиента: г. Казань, ул. Баумана, д. 7, кв. 3", "Баумана", "ADDRESS"),
         span("Адрес отделения: г. Москва, ул. Тверская, д. 10. Адрес клиента: г. Казань, ул. Баумана, д. 7, кв. 3", "7", "ADDRESS"),
         span("Адрес отделения: г. Москва, ул. Тверская, д. 10. Адрес клиента: г. Казань, ул. Баумана, д. 7, кв. 3", "3", "ADDRESS")],
        "ADDRESS", "office_vs_residential")

    # ------------------------------------------------------------------ CVV
    add("CVV 123",
        [span("CVV 123", "123", "CVV")],
        "CVV", "positive")
    add("код безопасности 456",
        [span("код безопасности 456", "456", "CVV")],
        "CVV", "positive")
    add("CVC 7890",
        [span("CVC 7890", "7890", "CVV")],
        "CVV", "positive")
    add("порт 8080", [], "CVV", "negative")
    add("год 2024", [], "CVV", "negative")

    # ------------------------------------------------------------------ PIN
    add("PIN: 4321",
        [span("PIN: 4321", "4321", "PIN")],
        "PIN", "positive")
    add("пин-код 1234",
        [span("пин-код 1234", "1234", "PIN")],
        "PIN", "positive")
    add("ПИН 5678",
        [span("ПИН 5678", "5678", "PIN")],
        "PIN", "positive")
    add("забыл PIN-код", [], "PIN", "negative")
    add("код 1234", [], "PIN", "negative")

    # ------------------------------------------------------------------ CARDHOLDER_NAME
    add("держатель карты Иван Петров",
        [span("держатель карты Иван Петров", "Иван Петров", "CARDHOLDER_NAME")],
        "CARDHOLDER_NAME", "positive")
    add("cardholder JOHN SMITH",
        [span("cardholder JOHN SMITH", "JOHN SMITH", "CARDHOLDER_NAME")],
        "CARDHOLDER_NAME", "positive")
    add("имя на карте Анна Иванова",
        [span("имя на карте Анна Иванова", "Анна Иванова", "CARDHOLDER_NAME")],
        "CARDHOLDER_NAME", "positive")
    add("держатель ООО Ромашка", [], "CARDHOLDER_NAME", "negative")

    # ------------------------------------------------------------------ OVERLAP: FULL_NAME vs CARDHOLDER_NAME on same fragment
    add("Клиент Иван Петров, держатель карты Иван Петров",
        [span("Клиент Иван Петров, держатель карты Иван Петров", "Иван Петров", "FULL_NAME"),
         span("Клиент Иван Петров, держатель карты Иван Петров", "Иван Петров", "CARDHOLDER_NAME", 20)],
        "FULL_NAME", "overlap")

    # ------------------------------------------------------------------ BARE_VALUE
    add("ivan.petrov@example.com",
        [span("ivan.petrov@example.com", "ivan.petrov@example.com", "EMAIL")],
        "EMAIL", "bare_value")
    add("+7 912 345-67-89",
        [span("+7 912 345-67-89", "+7 912 345-67-89", "PHONE")],
        "PHONE", "bare_value")
    add("4276189074144957",
        [span("4276189074144957", "4276189074144957", "CARD")],
        "CARD", "bare_value")
    add("119098818444",
        [span("119098818444", "119098818444", "INN")],
        "INN", "bare_value")

    # ------------------------------------------------------------------ CONTEXT DISAMBIGUATION (birth vs issue vs ordinary in one text)
    add("дата рождения 12.04.1990, паспорт выдан 15.03.2015, встреча 20.06.2020",
        [span("дата рождения 12.04.1990, паспорт выдан 15.03.2015, встреча 20.06.2020", "12.04.1990", "BIRTH_DATE"),
         span("дата рождения 12.04.1990, паспорт выдан 15.03.2015, встреча 20.06.2020", "15.03.2015", "PASSPORT_ISSUE_DATE")],
        "BIRTH_DATE", "overlap")

    return examples


def write(examples: list[dict], path: Path) -> None:
    """Write labeled fixtures to a path (reproducible, fixed seed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "seed": SEED,
        "generator": "scripts/generate_fixtures.py",
        "note": "Independent labeled synthetic fixtures. Ground truth from templates, not detector predictions.",
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
    sys.exit(main())
