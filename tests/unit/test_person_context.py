"""Regression cases for name boundaries and local public/private context.

Expected spans are hand-labelled synthetic examples, independent of detector
output. A public reference does not exempt an adjacent client or a namesake.
"""

import pytest

from app.detectors.person import FullNameDetector


@pytest.mark.parametrize("text", [
    "В банк обратился Александр Сергеевич Пушкин.",
    "Обратился в банк Александр Сергеевич Пушкин.",
])
def test_banking_action_before_historical_namesake(text):
    spans = [s for d in FullNameDetector().detect(text) for s in d.sensitive_spans]
    assert [text[s.start:s.end] for s in spans] == ["Александр Сергеевич Пушкин"]


def test_private_phone_action_overrides_profession():
    text = "Поэт Иван Петров сообщил свой номер телефона +7 918 123-45-67."
    spans = [s for d in FullNameDetector().detect(text) for s in d.sensitive_spans]
    assert [text[s.start:s.end] for s in spans] == ["Иван Петров"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "Клиент Александр Сергеевич Пушкин, телефон +7 918 123-45-67.",
            ["Александр Сергеевич Пушкин"],
            id="historical-namesake-client",
        ),
        pytest.param(
            "Клиент Александер Сергеевич Пушкин, телефон +7 918 123-45-67.",
            ["Александер Сергеевич Пушкин"],
            id="screenshot-uncommon-first-name",
        ),
        pytest.param(
            "Клиент Александр Сергеевич Пушкин просит закрыть счёт.",
            ["Александр Сергеевич Пушкин"],
            id="namesake-with-following-prose",
        ),
        pytest.param(
            "Клиент Иван Иванович Петров просит закрыть счёт.",
            ["Иван Иванович Петров"],
            id="client-with-following-prose",
        ),
        pytest.param(
            "ФИО: Иван Петров просит закрыть счёт.",
            ["Иван Петров"],
            id="fio-does-not-consume-following-prose",
        ),
        pytest.param(
            "Клиент читает роман и пишет заявление.",
            [],
            id="ordinary-word-roman-is-not-a-name",
        ),
        pytest.param(
            "Клиент Роман Иванов, телефон +7 918 123-45-67.",
            ["Роман Иванов"],
            id="roman-is-a-name-with-surname",
        ),
        pytest.param(
            "клиент иван иванович петров, телефон +7 918 123-45-67.",
            ["иван иванович петров"],
            id="lowercase-client",
        ),
        pytest.param(
            "КЛИЕНТ ИВАН ИВАНОВИЧ ПЕТРОВ, телефон +7 918 123-45-67.",
            ["ИВАН ИВАНОВИЧ ПЕТРОВ"],
            id="uppercase-client",
        ),
        pytest.param(
            "Клиент\u00a0Иван\u00a0Иванович\u00a0Петров, телефон +7 918 123-45-67.",
            ["Иван\u00a0Иванович\u00a0Петров"],
            id="nbsp-preserves-original-span",
        ),
        pytest.param(
            "Заявление от Кузнецовой Елены Андреевны.",
            ["Кузнецовой Елены Андреевны"],
            id="inflected-private-full-name",
        ),
        pytest.param(
            "Заявление клиента Александра Сергеевича Пушкина, телефон +7 918 123-45-67.",
            ["Александра Сергеевича Пушкина"],
            id="inflected-private-namesake",
        ),
        pytest.param(
            "Клиент Пушкин А. С., телефон +7 918 123-45-67.",
            ["Пушкин А. С."],
            id="surname-before-initials",
        ),
        pytest.param(
            "Клиент А. С. Пушкин, телефон +7 918 123-45-67.",
            ["А. С. Пушкин"],
            id="initials-before-surname",
        ),
        pytest.param(
            "Клиент А.С. Пушкин, телефон +7 918 123-45-67.",
            ["А.С. Пушкин"],
            id="compact-initials",
        ),
        pytest.param(
            "Поэт Александр Сергеевич Пушкин написал «Евгения Онегина».",
            [],
            id="explicit-public-reference",
        ),
        pytest.param(
            "Александр Сергеевич Пушкин написал роман «Евгений Онегин».",
            [],
            id="literary-context-without-poet-marker",
        ),
        pytest.param(
            "Я читаю произведения Александра Сергеевича Пушкина.",
            [],
            id="inflected-literary-reference",
        ),
        pytest.param(
            "Стихотворение А. С. Пушкина входит в школьную программу.",
            [],
            id="literary-initials",
        ),
        pytest.param(
            "Александр Сергеевич Пушкин — известный поэт.",
            [],
            id="public-evidence-after-name",
        ),
        pytest.param(
            "Поэт Александр Сергеевич Пушкин. Клиент Иван Иванович Петров.",
            ["Иван Иванович Петров"],
            id="public-before-private-sentence",
        ),
        pytest.param(
            "Клиент Иван Иванович Петров. Поэт Александр Сергеевич Пушкин.",
            ["Иван Иванович Петров"],
            id="private-before-public-sentence",
        ),
        pytest.param(
            "Поэт Александр Сергеевич Пушкин; клиент Иван Иванович Петров.",
            ["Иван Иванович Петров"],
            id="public-before-private-clause",
        ),
        pytest.param(
            "Клиент Иван Иванович Петров читает поэта Александра Сергеевича Пушкина.",
            ["Иван Иванович Петров"],
            id="private-and-public-in-one-clause",
        ),
        pytest.param(
            "Клиент Иван Иванович Петров; Александр Сергеевич Пушкин написал «Евгения Онегина».",
            ["Иван Иванович Петров"],
            id="private-before-literary-clause",
        ),
        pytest.param(
            "Клиент, поэт Сергей Николаевич Орлов, оставил заявление.",
            ["Сергей Николаевич Орлов"],
            id="client-occupation-does-not-exempt-name",
        ),
        pytest.param(
            "Клиент-поэт Сергей Орлов, телефон +7 918 123-45-67.",
            ["Сергей Орлов"],
            id="hyphenated-client-occupation",
        ),
        pytest.param(
            "Обсудим поэта. Клиент Иван Петров, паспорт 4509 123456.",
            ["Иван Петров"],
            id="public-word-in-previous-sentence",
        ),
        pytest.param(
            "Писатель Иван Сергеевич Тургенев. Клиент Сергей Орлов.",
            ["Сергей Орлов"],
            id="different-public-name-next-to-client",
        ),
        pytest.param(
            "Иванов Иван Иванович",
            ["Иванов Иван Иванович"],
            id="standalone-private-full-name",
        ),
        pytest.param(
            "Иван Петров написал мне о проблеме с платежом.",
            ["Иван Петров"],
            id="private-person-wrote-message",
        ),
        pytest.param(
            "Мне написал Иван Петров.",
            ["Иван Петров"],
            id="writing-verb-before-private-name",
        ),
        pytest.param(
            "Иван Петров читает роман.",
            ["Иван Петров"],
            id="reader-is-not-a-public-person",
        ),
        pytest.param(
            "Иван Петров прочитал «Анну Каренину».",
            ["Иван Петров"],
            id="reader-and-quoted-work",
        ),
        pytest.param(
            "Профессор Иван Петров прислал свой паспорт.",
            ["Иван Петров"],
            id="occupation-with-personal-passport-action",
        ),
        pytest.param(
            "Поэт Иван Петров сообщил паспорт 4509 123456.",
            ["Иван Петров"],
            id="public-role-with-sensitive-field-action",
        ),
        pytest.param(
            "Александр Сергеевич Пушкин обратился в банк с паспортом 4509 123456.",
            ["Александр Сергеевич Пушкин"],
            id="historical-namesake-personal-banking-action",
        ),
        pytest.param(
            "Льва Николаевича Толстого",
            [],
            id="irregular-inflected-historical-identity",
        ),
        pytest.param(
            "А. С. Пушкин",
            [],
            id="bare-historical-initials",
        ),
        pytest.param(
            "Фамилия: Пушкин, имя: Александр, отчество: Сергеевич.",
            ["Пушкин", "Александр", "Сергеевич"],
            id="separate-labelled-name-fields",
        ),
    ],
)
def test_name_sensitive_spans(text, expected):
    detections = FullNameDetector().detect(text)
    spans = sorted(
        (span.start, span.end)
        for detection in detections
        for span in detection.sensitive_spans
    )
    assert [text[start:end] for start, end in spans] == expected
    assert all(left[1] <= right[0] for left, right in zip(spans, spans[1:]))
