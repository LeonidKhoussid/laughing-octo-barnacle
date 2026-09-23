"""A small labeled synthetic regression set for Trust Lab Action B.

These examples are synthetic and hand-checked. They are independent of the
detector predictions (section 16.1). The gold spans are in ORIGINAL coordinates.
This set is used to compare the active policy vs a candidate policy on the SAME
labeled examples (section 17.2 Action B).
"""
from __future__ import annotations

from app.trust_lab.mutations import GoldSpan, LabeledExample

REGRESSION_SET_ID = "trust-lab-regression-001"
REGRESSION_SEED = 20260922


def _g(text: str, needle: str, category: str) -> GoldSpan:
    start = text.index(needle)
    return GoldSpan(start, start + len(needle), category)


def build_regression_set() -> list[LabeledExample]:
    """Return the labeled synthetic regression set (17 categories)."""
    examples: list[LabeledExample] = []

    # FULL_NAME, EMAIL, PHONE, CARD
    t1 = "Клиент Иван Петров, email ivan@example.com, телефон +7 912 345-67-89, карта 4276189074144957"
    examples.append(
        LabeledExample(
            text=t1,
            gold_spans=[
                _g(t1, "Иван Петров", "FULL_NAME"),
                _g(t1, "ivan@example.com", "EMAIL"),
                _g(t1, "+7 912 345-67-89", "PHONE"),
                _g(t1, "4276189074144957", "CARD"),
            ],
            service_words=["Клиент", "email", "телефон", "карта"],
        )
    )

    # BIRTH_DATE, PASSPORT, INN
    t2 = "Иван Петров, дата рождения 12.04.1990, паспорт серия 0318 номер 123456, ИНН 7707083893"
    examples.append(
        LabeledExample(
            text=t2,
            gold_spans=[
                _g(t2, "Иван Петров", "FULL_NAME"),
                _g(t2, "12.04.1990", "BIRTH_DATE"),
                _g(t2, "0318", "PASSPORT"),
                _g(t2, "123456", "PASSPORT"),
                _g(t2, "7707083893", "INN"),
            ],
            service_words=["дата рождения", "паспорт", "серия", "номер", "ИНН"],
        )
    )

    # ADDRESS, BIRTH_PLACE, CITIZENSHIP
    t3 = "Место рождения г. Москва, гражданство Российская Федерация, адрес г. Москва, ул. Тверская, д. 15, кв. 42"
    examples.append(
        LabeledExample(
            text=t3,
            gold_spans=[
                _g(t3, "г. Москва", "BIRTH_PLACE"),
                _g(t3, "Российская Федерация", "CITIZENSHIP"),
                _g(t3, "г. Москва, ул. Тверская, д. 15, кв. 42", "ADDRESS"),
            ],
            service_words=["Место рождения", "гражданство", "адрес"],
        )
    )

    # PASSPORT_ISSUER, DEPARTMENT_CODE, PASSPORT_ISSUE_DATE, DRIVER_LICENSE
    t4 = "Паспорт выдан ОВД района, код подразделения 770-031, дата выдачи 15.03.2015, водительское удостоверение 77 12 345678"
    examples.append(
        LabeledExample(
            text=t4,
            gold_spans=[
                _g(t4, "ОВД района", "PASSPORT_ISSUER"),
                _g(t4, "770-031", "DEPARTMENT_CODE"),
                _g(t4, "15.03.2015", "PASSPORT_ISSUE_DATE"),
                _g(t4, "77 12 345678", "DRIVER_LICENSE"),
            ],
            service_words=["Паспорт выдан", "код подразделения", "дата выдачи", "водительское удостоверение"],
        )
    )

    # CVV, PIN, CARDHOLDER_NAME
    t5 = "Держатель карты Иван Петров, CVV 123, PIN 4321"
    examples.append(
        LabeledExample(
            text=t5,
            gold_spans=[
                _g(t5, "Иван Петров", "CARDHOLDER_NAME"),
                _g(t5, "123", "CVV"),
                _g(t5, "4321", "PIN"),
            ],
            service_words=["Держатель карты", "CVV", "PIN"],
        )
    )

    return examples
