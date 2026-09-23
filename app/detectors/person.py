"""FULL_NAME detector: Russian name grammar/dictionary, initials, case/register.

Bounded Russian name grammar with local public/private context. This is a
heuristic detector, not a general entity linker or exhaustive public-person list.
"""
from __future__ import annotations

import re
import uuid

from app.core.models import Decision, Detection, Signal, Span
from app.detectors.base import Detector

# Common Russian first names (nominative).
_FIRST_NAMES = {
    "иван", "петр", "пётр", "александр", "алексей", "дмитрий", "сергей", "андрей",
    "михаил", "николай", "владимир", "павел", "артем", "артём", "максим", "егор",
    "кирилл", "олег", "игорь", "виктор", "юрий", "антон", "вадим", "григорий",
    "анатолий", "василий", "геннадий", "евгений", "илья", "константин", "леонид",
    "роман", "станислав", "тимофей", "федор", "фёдор", "эдуард", "ярослав",
    "анна", "мария", "елена", "ольга", "наталья", "татьяна", "ирина", "светлана",
    "екатерина", "юлия", "оксана", "людмила", "галина", "нина", "вера", "надежда",
    "любовь", "валентина", "валерия", "дария", "дарья", "алина", "полина",
    "ксения", "виктория", "александра", "марина", "екатерина",
}

# Common Russian patronymics (masculine/feminine).
_PATRONYMICS = {
    "иванович", "ивановна", "петрович", "петровна", "александрович", "александровна",
    "алексеевич", "алексеевна", "дмитриевич", "дмитриевна", "сергеевич", "сергеевна",
    "андреевич", "андреевна", "михайлович", "михайловна", "николаевич", "николаевна",
    "владимирович", "владимировна", "павлович", "павловна", "артемович", "артёмович",
    "максимович", "максимовна", "егорович", "егоровна", "кириллович", "кирилловна",
    "олегович", "олеговна", "игоревич", "игоревна", "викторович", "викторовна",
    "юрьевич", "юрьевна", "антонович", "антоновна", "вадимович", "вадимовна",
    "григорьевич", "григорьевна", "анатольевич", "анатольевна", "васильевич",
    "васильевна", "геннадьевич", "геннадьевна", "евгеньевич", "евгеньевна",
    "ильич", "ильинична", "константинович", "константиновна", "леонидович",
    "леонидовна", "романович", "романовна", "станиславович", "станиславовна",
    "тимофеевич", "тимофеевна", "федорович", "фёдорович", "федоровна", "фёдоровна",
    "эдуардович", "эдуардовна", "ярославович", "ярославовна",
}

# Common Russian surnames (nominative, masculine/feminine).
_SURNAMES = {
    "иванов", "иванова", "петров", "петрова", "сидоров", "сидорова", "смирнов",
    "смирнова", "кузнецов", "кузнецова", "попов", "попова", "васильев", "васильева",
    "соколов", "соколова", "михайлов", "михайлова", "новов", "новова", "федоров",
    "фёдоров", "федорова", "фёдорова", "морозов", "морозова", "волков", "волкова",
    "алексеев", "алексеева", "лебедев", "лебедева", "семенов", "семёнов", "семенова",
    "семёнова", "егоров", "егорова", "павлов", "павлова", "козлов", "козлова",
    "степанов", "степанова", "николаев", "николаева", "орлов", "орлова", "андреев",
    "андреева", "макаров", "макарова", "никитин", "никитина", "захаров", "захарова",
    "зайцев", "зайцева", "соловьев", "соловьёв", "соловьева", "соловьёва",
    "борисов", "борисова", "яковлев", "яковлева", "григорьев", "григорьева",
    "романов", "романова", "воробьев", "воробьёв", "воробьева", "воробьёва",
    "сергеев", "сергеева", "фролов", "фролова", "александров", "александрова",
    "дмитриев", "дмитриева", "королев", "королёв", "королева", "королёва",
    "гусев", "гусева", "киселев", "киселёв", "киселева", "киселёва",
    "ильин", "ильина", "максимов", "максимова", "голубев", "голубева",
    "виноградов", "виноградова", "козлов", "козлова", "медведев", "медведева",
    "антонов", "антонова", "тарасов", "тарасова", "жиров", "жирова",
    "белов", "белова", "комаров", "комарова", "давыдов", "давыдова",
    "мельников", "мельникова", "щеглов", "щеглова", "коновалов", "коновалова",
    "кислов", "кислова", "чернов", "чернова", "капустин", "капустина",
    "кириллов", "кириллова", "громов", "громова", "быков", "быкова",
    "мальцев", "мальцева", "савельев", "савельева", "осипов", "осипова",
    "титов", "титова", "кравцов", "кравцова", "захаров", "захарова",
    "наумов", "наумова", "кудрявцев", "кудрявцева", "баранов", "баранова",
    "куликов", "куликова", "алексеев", "алексеева", "степанов", "степанова",
    "яковлев", "яковлева", "сорокин", "сорокина", "сергеев", "сергеева",
    "романенко", "шевченко", "коваленко", "бондаренко", "кравченко", "морозенко",
}

# Tokenize Unicode letters without normalizing the text: decomposed accents
# retain their original offsets. Hyphens and apostrophes stay inside one name.
_LETTER = r"[^\W\d_]"
_NAME_PART = rf"{_LETTER}(?:{_LETTER}|[\u0300-\u036f])*"
_NAME_TOKEN = re.compile(rf"{_LETTER}\.|{_NAME_PART}(?:[-‑–'’]{_NAME_PART})*")


def _forms(names: set[str]) -> set[str]:
    """Small, explicit case paradigm, not arbitrary prefix matching."""
    result = set(names)
    for name in names:
        if name == "лев":
            result.update({"льва", "льву", "львом", "льве"})
        if name.endswith(("ский", "цкий", "ый", "ой")):
            result.update(name[:-2] + ending for ending in ("ого", "ому", "им", "ым", "ом", "ая", "ой", "ую"))
        elif name.endswith("а"):
            result.update(name[:-1] + ending for ending in ("ы", "и", "е", "у", "ой", "ою"))
        elif name.endswith("я"):
            result.update(name[:-1] + ending for ending in ("и", "е", "ю", "ей", "ею"))
        elif name.endswith(("й", "ь")):
            result.update(name[:-1] + ending for ending in ("я", "ю", "ем", "е", "и", "ью"))
        else:
            result.update(name + ending for ending in ("а", "у", "ом", "е", "ы", "ов", "ам", "ами", "ах"))
    return result


_FIRST_FORMS = _forms(_FIRST_NAMES | {"лев", "борис", "денис", "артур", "семен", "семён"})
_FIRST_FORMS.update({"льва", "льву", "львом", "льве"})
_PATRONYMIC_FORMS = _forms(_PATRONYMICS)
_SURNAME_FORMS = _forms(_SURNAMES)
_PATRONYMIC = re.compile(r"[а-яё-]+(?:ович|евич|ич)(?:а|у|ем|ом|е)?|[а-яё-]+(?:овн|евн|ичн)(?:а|ы|е|у|ой)")
_SURNAME = re.compile(
    r"[а-яё-]{2,}(?:(?:ов|ев|ёв|ин|ын)(?:а|у|ом|е|ой|ы|у)?|"
    r"(?:ск|цк)(?:ий|ая|ого|ому|им|ом|ой|ую)|енко|ко|ук|юк|ич|ых|их)"
)

_PERSON_CONTEXT = re.compile(
    r"(?i)\b(?:клиент(?:а|у|ом|е|ка|ки)?|гражданин|гражданина|гражданка|"
    r"пациент(?:а|у|ом|ка|ки)?|сотрудник(?:а|у|ом)?|сотрудница|"
    r"заявител(?:ь|я|ю|ем)|заявительница|пользовател(?:ь|я|ю|ем)|"
    r"получател(?:ь|я|ю|ем|е|ьница|ьницы|ьнице|ьницу|ьницей)|"
    r"заказчик(?:а|у|ом|е)?|заказчиц(?:а|ы|е|у|ей)|"
    r"абонент(?:а|у|ом)?|владелец|владельца|владелица|заявление)\b"
)
_FIO_CONTEXT = re.compile(
    r"(?i)(?:\bф\.\s*и\.\s*о\.|\b(?:фио|фамилия|имя|отчество)\b)"
    r"(?:\s+(?:клиента|пациента|заявителя|пользователя|получателя|получательницы|"
    r"заказчика|заказчицы|сотрудника))?"
    r"(?:\s+(?:записан[оа]?|указан[оа]?))?(?:\s+(?:латиницей|кириллицей))?"
)
_PUBLIC_PERSON_CONTEXT = re.compile(
    r"(?i)\b(?:поэт(?:а|у|ом|е|ы|ов)?|писател(?:ь|я|ю|ем|е|и|ей)|писательниц[аыеу]|"
    r"художник(?:а|у|ом|е|и|ов)?|композитор(?:а|у|ом|е|ы|ов)?|"
    r"уч[её]н(?:ый|ого|ому|ым|ом)|акт[её]р(?:а|у|ом|е)?|актрис[аыеу]|"
    r"режисс[её]р(?:а|у|ом|е)?|скульптор(?:а|у|ом|е)?|архитектор(?:а|у|ом|е)?|"
    r"философ(?:а|у|ом|е)?|историк(?:а|у|ом|е)?|певец|певца|певица|"
    r"музыкант(?:а|у|ом|е)?|драматург(?:а|у|ом|е)?|публицист(?:а|у|ом|е)?|"
    r"президент(?:а|у|ом|е)?|министр(?:а|у|ом|е)?|император(?:а|у|ом|е)?|"
    r"академик(?:а|у|ом|е)?|профессор(?:а|у|ом|е)?)\b"
)
_CARDHOLDER_CONTEXT = re.compile(r"(?i)\b(?:держатель(?:ница)?(?:\s+карты)?|cardholder|имя\s+на\s+карт[ео]ч?к?е?)\b")
# Only short grammatical bridges attach a label to a person. Names, verbs and
# sentence boundaries break attachment, so one person's role cannot exempt another.
_BRIDGE_WORDS = {"от", "это", "является", "известный", "известного", "известному",
                 "великий", "великого", "великим", "русский", "русского", "русским",
                 "российский", "российского", "советский", "советского", "наш", "нашего"}
_PRIVATE_FIELD = re.compile(r"(?i)^\s*[,:(—–-]?\s*(?:телефон|мобильный|паспорт|карта|email|e-mail|инн)\b")
_LITERARY_CONTEXT = re.compile(
    r"(?i)\b(?:роман(?:а|у|ом|е)?|поэм[аыуе]|стих(?:и|ов|ами|ах)?|"
    r"стихотворени(?:е|я|й|ю|ем|ях)|произведени(?:е|я|й|ю|ем|ях)|"
    r"творчеств[оауе])\b"
)
_WORK_TITLE = re.compile(r"(?i)\b(?:написал[аи]?|прочитал[аи]?|читаю|читает|роман[аеу]?|поэм[ауы]|книг[ауы])\s*[«\"]\s*$")
_PRIVATE_ACTION = re.compile(
    r"(?i)^\s*(?:сообщил[аи]?|прислал[аи]?|предоставил[аи]?|предъявил[аи]?|"
    r"указал[аи]?|оставил[аи]?)\s+(?:сво[йюи]\s+)?(?:паспорт|телефон|карт[уы]|инн|заявление|данные|номер\s+(?:телефона|карты|паспорта))\b|"
    r"^\s*(?:обратил(?:ся|ась)|приш[её]л|пришла)\s+в\s+банк\b|"
    r"^\s*(?:просит|попросил[аи]?)\s+(?:закрыть|открыть|заблокировать)\s+(?:сч[её]т|карт[уы])\b"
)
_PRIVATE_BEFORE = re.compile(r"(?i)\b(?:в\s+банк\s+обратил(?:ся|ась)|обратил(?:ся|ась)\s+в\s+банк)\s*$")
# A bounded set of historical identities handles bare public references too.
# This is not a surname whitelist: a directly attached client label or private
# field always takes precedence, and unfamiliar people need public context.
_HISTORICAL_IDENTITIES = (
    ("александр", "сергеевич", "пушкин"),
    ("лев", "николаевич", "толстой"),
    ("федор", "михайлович", "достоевский"),
    ("антон", "павлович", "чехов"),
    ("михаил", "юрьевич", "лермонтов"),
)
_HISTORICAL_FORMS = tuple(tuple(_forms({word}) for word in identity) for identity in _HISTORICAL_IDENTITIES)
for _first, _patronymic, _last in _HISTORICAL_FORMS:
    _FIRST_FORMS.update(_first)
    _PATRONYMIC_FORMS.update(_patronymic)
    _SURNAME_FORMS.update(_last)


def _bridge(value: str) -> bool:
    if re.search(r"[.!?;\n\r]", value):
        return False
    value = _PUBLIC_PERSON_CONTEXT.sub(" ", value)
    words = re.findall(r"[а-яё]+", value.lower())
    return len(words) <= 3 and all(word in _BRIDGE_WORDS for word in words) and not re.search(r"[0-9A-Za-z]", value)


def _attached(pattern: re.Pattern, text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 100):start]
    after = text[end:end + 70]
    return (any(_bridge(before[m.end():]) for m in pattern.finditer(before))
            or any(_bridge(after[:m.start()]) for m in pattern.finditer(after)))


def _identity_part(word: str, forms: set[str]) -> bool:
    return word in forms or (len(word) == 2 and word.endswith(".") and any(n.startswith(word[0]) for n in forms))


def _historical_name(words: list[str]) -> bool:
    for first, patronymic, last in _HISTORICAL_FORMS:
        if len(words) == 3 and ((_identity_part(words[0], first) and _identity_part(words[1], patronymic) and words[2] in last)
                               or (words[0] in last and _identity_part(words[1], first) and _identity_part(words[2], patronymic))):
            return True
        if len(words) == 2 and ((words[0] in first and words[1] in last) or (words[0] in last and words[1] in first)):
            return True
    return False


def _private_context(text: str, start: int, end: int) -> bool:
    return (_attached(_PERSON_CONTEXT, text, start, end)
            or bool(_PRIVATE_BEFORE.search(text[max(0, start - 90):start]))
            or bool(_PRIVATE_FIELD.match(text[end:end + 50]))
            or bool(_PRIVATE_ACTION.match(text[end:end + 90])))


def _public_reference(text: str, start: int, end: int, words: list[str]) -> bool:
    work_title = (bool(_WORK_TITLE.search(text[max(0, start - 60):start]))
                  and text[end:end + 10].lstrip().startswith(("»", '"')))
    return (_attached(_PUBLIC_PERSON_CONTEXT, text, start, end)
            or _attached(_LITERARY_CONTEXT, text, start, end)
            or work_title or _historical_name([w.lower().replace("ё", "е") for w in words]))


def has_public_subject(text: str, predicate_start: int) -> bool:
    """Share local subject classification with narrative birth detectors."""
    tokens = list(_NAME_TOKEN.finditer(text, max(0, predicate_start - 140), predicate_start))
    if not tokens or text[tokens[-1].end():predicate_start].strip():
        return False
    for length in (3, 2, 1):
        run = tokens[-length:]
        if len(run) != length or any(text[a.end():b.start()].strip() for a, b in zip(run, run[1:])):
            continue
        start, end = run[0].start(), run[-1].end()
        if _private_context(text, start, end) or _attached(_FIO_CONTEXT, text, start, end):
            return False
        if _public_reference(text, start, end, [m.group() for m in run]):
            return True
    return False


def _roles(word: str) -> set[str]:
    low = word.lower().replace("ё", "е").replace("‑", "-").replace("–", "-")
    roles = set()
    if re.fullmatch(r"[а-я]\.", low):
        return {"initial"}
    if low in _FIRST_FORMS:
        roles.add("first")
    if low in _PATRONYMIC_FORMS or _PATRONYMIC.fullmatch(low):
        roles.add("patronymic")
    if low in _SURNAME_FORMS or _SURNAME.fullmatch(low):
        roles.add("last")
    return roles


def _plausible(words: list[str], personal: bool, fio: bool) -> bool:
    roles = [_roles(word) for word in words]
    # Unknown capitalized tokens may fill ONE missing role in an otherwise
    # grammatical name next to an explicit client/ФИО label (e.g. Александер).
    if personal or fio:
        for word, role in zip(words, roles):
            if not role and word[0].isupper() and not _PERSON_CONTEXT.fullmatch(word) and not _PUBLIC_PERSON_CONTEXT.fullmatch(word):
                role.add("unknown")
    patterns = (
        (("first", "patronymic", "last"), ("last", "first", "patronymic"),
         ("last", "initial", "initial"), ("initial", "initial", "last"))
        if len(words) == 3 else
        (("first", "last"), ("last", "first"), ("first", "patronymic"),
         ("last", "initial"), ("initial", "last"))
    )
    for pattern in patterns:
        missing = [i for i, required in enumerate(pattern) if required not in roles[i]]
        if not missing or (len(missing) == 1 and "unknown" in roles[missing[0]]
                           and (len(words) == 3 or "initial" not in pattern)):
            return True
        if (personal and len(words) == 3 and pattern == ("first", "patronymic", "last")
                and missing == [0] and not roles[0]):
            return True
    # A patronymic anchors a three-part Russian name even when both the first
    # name and surname are unfamiliar. Still require an explicit name/role
    # label and capitalized parts; don't extend into following lowercase prose.
    return fio and (
        all(role == {"unknown"} for role in roles)
        or (len(words) == 3 and all(word[0].isupper() for word in words)
            and any("patronymic" in role for role in roles[1:]))
    )


class FullNameDetector(Detector):
    detector_id = "full_name"
    detector_version = "1.3.0"

    def detect(self, text: str) -> list[Detection]:
        out: list[Detection] = []
        tokens = list(_NAME_TOKEN.finditer(text))
        i = 0
        while i < len(tokens):
            matched = False
            for length in (3, 2):
                run = tokens[i:i + length]
                if len(run) != length or any(text[a.end():b.start()].strip() for a, b in zip(run, run[1:])):
                    continue
                words = [m.group() for m in run]
                start, end = run[0].start(), run[-1].end()
                if any(_PERSON_CONTEXT.fullmatch(w) or _FIO_CONTEXT.fullmatch(w) or _PUBLIC_PERSON_CONTEXT.fullmatch(w) for w in words):
                    continue
                if not _plausible(words, True, True):
                    continue
                personal = _private_context(text, start, end)
                fio = _attached(_FIO_CONTEXT, text, start, end)
                public = _public_reference(text, start, end, words)
                # A directly attached person role or name field supplies the
                # structure for unfamiliar names; capitalization alone does not.
                if not _plausible(words, personal or public, fio or personal):
                    continue
                # Consume the whole candidate even when public: don't rediscover
                # its patronymic/surname as an unrelated private two-word name.
                matched = True
                cardholder = _attached(_CARDHOLDER_CONTEXT, text, start, end)
                if not cardholder and not (public and not (personal or fio)):
                    span = Span(start, end)
                    out.append(Detection(
                        entity_id=str(uuid.uuid4()), category="FULL_NAME",
                        evidence_spans=(span,), sensitive_spans=(span,),
                        signals=(Signal("personal_context" if personal or fio else "name_grammar", 1.0),),
                        detector_id=self.detector_id, detector_version=self.detector_version,
                        score=0.85 if personal or fio else 0.7, decision=Decision.MASK,
                        rule_id="full-name-context-grammar" if personal or fio else "full-name-bare-strong",
                    ))
                i += length
                break
            if not matched:
                i += 1
        # Explicit separate fields contain a single value, not a 2–3 word name.
        for match in re.finditer(r"(?i)\b(?:фамилия|имя|отчество)\s*:\s*([а-яё]+(?:[-‑–][а-яё]+)*)", text):
            start, end = match.span(1)
            if not (_roles(match.group(1)) or match.group(1)[0].isupper()):
                continue
            if any(d.sensitive_spans[0].start <= start < d.sensitive_spans[0].end for d in out):
                continue
            span = Span(start, end)
            out.append(Detection(
                entity_id=str(uuid.uuid4()), category="FULL_NAME",
                evidence_spans=(Span(match.start(), end),), sensitive_spans=(span,),
                signals=(Signal("personal_context", 1.0),),
                detector_id=self.detector_id, detector_version=self.detector_version,
                score=0.85, decision=Decision.MASK, rule_id="full-name-labeled-field",
            ))
        out.sort(key=lambda detection: detection.sensitive_spans[0].start)
        return out
