"""Local entity recognition and conservative, candidate-specific context decisions.

NER finds entities; NLI evaluates their use in a bounded passage. Neither model
is a public-person database. Explicit personal records override public context,
and uncertain candidates stay protected. No request text is cached or exported.
"""
from __future__ import annotations

import re
import uuid
import bisect
from dataclasses import replace

from app.core.models import Decision, Detection, Signal, Span
from app.detectors.base import Detector
from app.detectors.person import _attached, _FIO_CONTEXT, _private_context, _plausible, _historical_name
from app.nlp.model_runtime import get_runtime


_ROLES = re.compile(r"(?i)^(?:поэт|писатель|драматург|клиент|сотрудник|президент|министр|актёр|актер|профессор|академик|за[её]мщик|анкета|паспорт|роман|телефон|адрес|дата|почта)$")
_PRIVATE = re.compile(
    r"(?i)\b(?:клиент\w*|за[её]мщик\w*|пациент\w*|анкета|анкет[уы]|"
    r"паспорт\w*|снилс|личн\w*\s+(?:телефон|адрес|почт)|домашн\w*\s+адрес|"
    r"прожива\w*|зарегистрирован\w*|утечк\w*|слив\w*|досье|резюме|"
    r"номер\s+карт\w*|персональн\w*\s+данн\w*)\b"
)
_PRIVATE_AFTER = re.compile(
    r"(?i)^\s*(?:[,—–:-]\s*)?(?:написал\w*\s+(?:мне|нам|ему|ей)|"
    r"читает|читаю|прочитал\w*|позвонил\w*|прислал\w*|отправил\w*|"
    r"заполнил\w*|подал\w*\s+заяв\w*|прожива\w*|сообщил\w*|готовит\s+доклад|попросил\w*\s+изменить|"
    r"обратил\w*\s+в\s+банк|работает\s+(?:врачом|инженером))\b"
)
_SCIENCE = re.compile(r"(?i)\b(?:наук\w*|научн\w*|теори\w*|алгоритм\w*|информатик\w*|опыт\w*|откры\w*|изобрет\w*|физик\w*|химик\w*|математик\w*|нобел\w*|учебник\w*|лекци\w*|рефлекс\w*|систем\w*\s+элемент\w*)\b")
_CULTURE = re.compile(r"(?i)\b(?:поэт\w*|писател\w*|драматург\w*|роман\w*|поэм\w*|стих\w*|книг\w*|произведени\w*|симфони\w*|композитор\w*|художник\w*|музык\w*|картин\w*|пьес\w*|геро[йяев]+|персонаж\w*|литератур\w*|автор\w*|рецензи\w*|певец|певиц\w*|концерт\w*|опер[аыуе])\b")
_PUBLIC_EVENT = re.compile(r"(?i)\b(?:новост\w*|газет\w*|интервью|опубликован\w*|выступ\w*|президент\w*|министр\w*|основал\w*|основател\w*|чемпион\w*|олимпий\w*|истор\w*|биограф\w*|энциклопеди\w*|спортсмен\w*|турнир\w*|конференци\w*)\b")
_INSTITUTION = re.compile(r"(?i)\b(?:музе\w*|театр\w*|библиотек\w*|университет\w*|памятник\w*|монумент\w*|проспект\w*|площад\w*|отделени\w*|филиал\w*|организаци\w*|компани\w*|служб\w*\s+поддержки|горяч\w*\s+лини\w*)\b")
_CONTACT = re.compile(r"(?i)\b(?:официальн\w*|справочн\w*|поддержк\w*|горяч\w*\s+лини\w*|пресс-служб\w*|контакт\w*\s+(?:компани\w*|организаци\w*))\b")
_EDUCATION = re.compile(r"(?i)\b(?:учебник\w*|справочник\w*|на\s+(?:уроке|лекции)|учебн\w*\s+материал\w*)\b")
_ATTRIBUTION = re.compile(r"(?i)\b(?:алгоритм\w*|теори\w*|опыт\w*|теорем\w*|роман\w*|стих\w*|пьес\w*|симфони\w*|произведени\w*)\s+$")
_PRIVATE_RECORD = re.compile(r"(?i)\b(?:анкет\w*|заявлен\w*|кредитн\w*\s+заявк\w*|досье|crm)\b")
_ABBREVIATIONS = {"г", "ул", "д", "кв", "стр", "корп", "им", "пр", "пер", "обл", "р", "т"}
_ELIGIBLE = {"FULL_NAME", "BIRTH_DATE", "BIRTH_PLACE", "ADDRESS", "EMAIL", "PHONE", "CITIZENSHIP"}


def _sentences(text: str) -> list[tuple[int, int]]:
    boundaries = [0]
    for match in re.finditer(r"[.!?;]\s+|\n+", text):
        if match.group().startswith("."):
            word = re.search(r"([А-ЯЁа-яёA-Za-z]+)$", text[max(0, match.start()-12):match.start()])
            if word and (len(word.group()) == 1 or word.group().lower() in _ABBREVIATIONS):
                continue
        boundaries.append(match.end())
    boundaries.append(len(text))
    return [(a, b) for a, b in zip(boundaries, boundaries[1:]) if a < b]


def _detection(span: Span, score: float) -> Detection:
    return Detection(
        entity_id=str(uuid.uuid4()), category="FULL_NAME", evidence_spans=(span,),
        sensitive_spans=(span,), signals=(Signal("local_bert_person", score),),
        detector_id="semantic_context", detector_version="1.0.0", score=score,
        decision=Decision.MASK, rule_id="bert-person-protected-by-default",
    )


class SemanticDetector(Detector):
    detector_id = "semantic_context"
    detector_version = "1.1.0"

    def __init__(self, model_dir: str) -> None:
        self.runtime = get_runtime(model_dir)

    def detect(self, text: str) -> list[Detection]:
        return self.refine(text, [])

    def refine(self, text: str, detections: list[Detection]) -> list[Detection]:
        entities = self.runtime.ner(text)
        candidates = list(detections)
        # NER supplements unfamiliar names. Existing structured detectors retain
        # their exact boundaries and categories (e.g. cardholder or passport issuer).
        for entity in entities:
            if entity.label != "PER" or entity.score < 0.60:
                continue
            start, end = entity.start, entity.end
            while start < end and text[start] in " «\"'([{\n\r\t":
                start += 1
            while end > start and text[end-1] in " .,:;!?»\"'\n\r\t":
                end -= 1
            value = text[start:end]
            if not value or _ROLES.fullmatch(value) or value.lower() in {"от", "для", "на", "в", "к", "из", "о", "об", "по", "и", "с"}:
                continue
            # Reject labels on subword fragments ('За' inside 'Заёмщик') and
            # pieces of email/opaque tokens. A token classifier is not an oracle.
            if ((start and (text[start-1].isalnum() or text[start-1] in "_@"))
                    or (end < len(text) and (text[end].isalnum() or text[end] in "_@"))):
                continue
            span = Span(start, end)
            if any(d.category != "FULL_NAME" and any(s.overlaps(span) for s in d.sensitive_spans) for d in detections):
                continue
            if any(d.category == "FULL_NAME" and any(s.start <= start and s.end >= end for s in d.sensitive_spans) for d in detections):
                continue
            candidates.append(_detection(span, entity.score))

        # Join a NER first name to a grammatical patronymic/surname when both
        # describe one contiguous 2–3-word name. Never merge two whole people.
        name_spans = sorted({(s.start, s.end) for d in candidates if d.category == "FULL_NAME" for s in d.sensitive_spans})
        for (a, b), (c, d) in zip(name_spans, name_spans[1:]):
            words = text[a:d].split()
            if b < c and not text[b:c].strip() and len(words) in (2, 3) and _plausible(words, True, False):
                candidates.append(_detection(Span(a, d), 0.7))

        sentences = _sentences(text)
        sentence_starts = [a for a, _ in sentences]
        # Each sensitive component gets its own decision. An address detector
        # can emit components belonging to different people/places in one call.
        decisions: dict[tuple[str, int, int], tuple[str, float]] = {}
        jobs: dict[tuple[str, int, int], tuple[str, list[str]]] = {}
        for detection in candidates:
            if detection.category not in _ELIGIBLE:
                continue
            for span in detection.sensitive_spans:
                key = detection.category, span.start, span.end
                if key in decisions or key in jobs:
                    continue
                begin, finish = sentences[max(0, bisect.bisect_right(sentence_starts, span.start)-1)]
                # Long sentences are inspected in a local window; this does not
                # truncate detection: NER and deterministic scanners cover all text.
                begin, finish = max(begin, span.start-240), min(finish, span.end+240)
                passage = text[begin:finish].strip()
                value = text[span.start:span.end]
                # A named work inside another person's record has its own
                # subject: 'доклад об опытах Менделя' does not identify the
                # employee preparing that report. Keep the public relation
                # local; explicit client/record labels still take precedence.
                attribution = _ATTRIBUTION.search(text[begin:span.start])
                if (detection.category == "FULL_NAME" and attribution
                        and not _PRIVATE_RECORD.search(passage)):
                    passage = text[begin + attribution.start():span.end]
                if self._private(text, span, detection.category, passage):
                    decisions[key] = "private-record", 1.0
                    continue
                # Preserve the existing bounded historical-reference policy;
                # novel names use NER/context below, not this small reference set.
                if detection.category == "FULL_NAME" and _historical_name(value.lower().replace("ё", "е").split()):
                    decisions[key] = "public-context", 1.0
                    continue
                # A dedication inside an institution's name identifies the
                # institution, not a current customer's personal record.
                if (detection.category == "FULL_NAME" and _INSTITUTION.search(passage)
                        and re.search(r"(?i)\b(?:имени|им\.)\s*$", text[max(begin, span.start-20):span.start])):
                    decisions[key] = "public-context", 1.0
                    continue
                hypotheses = self._hypotheses(value, detection.category, passage)
                if hypotheses:
                    jobs[key] = passage, hypotheses
                else:
                    decisions[key] = "uncertain-protected", 0.0

        # Deduplicate only within this request, so components sharing a passage
        # do not repeat transformer work. No text survives the call.
        decisions.update(self._context_decisions(jobs))

        result = []
        for detection in candidates:
            if detection.category not in _ELIGIBLE:
                result.append(detection)
                continue
            retained = []
            for span in detection.sensitive_spans:
                reason, score = decisions[detection.category, span.start, span.end]
                if reason == "public-context":
                    result.append(replace(detection, entity_id=str(uuid.uuid4()), sensitive_spans=(),
                                          evidence_spans=(span,), decision=Decision.KEEP,
                                          signals=(*detection.signals, Signal("local_nli_public_context", score, "negative")),
                                          rule_id="semantic-public-context"))
                else:
                    retained.append(span)
            if retained:
                result.append(replace(detection, sensitive_spans=tuple(retained)))
        return result

    def _context_decisions(self, jobs: dict) -> dict:
        # The policy is an OR over hypotheses. Once one qualifies, evaluating
        # the others cannot change MASK/KEEP. Short-circuit that OR while still
        # exhausting every hypothesis for unsupported/uncertain passages.
        # Scores and raw text live only within this request, never in a cache.
        pending = dict(jobs)
        decisions = {}
        scores = {}
        for index in range(max((len(hypotheses) for _, hypotheses in jobs.values()), default=0)):
            pairs = list(dict.fromkeys(
                (passage, hypotheses[index]) for passage, hypotheses in pending.values()
                if index < len(hypotheses) and (passage, hypotheses[index]) not in scores
            ))
            if pairs:
                scores.update(zip(pairs, self.runtime.nli(pairs)))
            for key, (passage, hypotheses) in list(pending.items()):
                e, n, c = scores[passage, hypotheses[index]]
                explicit_reference = bool(_EDUCATION.search(passage)) and not _PRIVATE.search(passage)
                threshold, margin = (0.55, 0.10) if explicit_reference else (0.80, 0.50)
                if e >= threshold and e - max(n, c) >= margin:
                    # Record the actual qualifying evidence, not an invented
                    # maximum over hypotheses that were never evaluated.
                    decisions[key] = "public-context", e
                    del pending[key]
                elif index + 1 == len(hypotheses):
                    decisions[key] = "uncertain-protected", 0.0
                    del pending[key]
            if not pending:
                break
        return decisions

    @staticmethod
    def _private(text: str, span: Span, category: str, passage: str) -> bool:
        if category == "FULL_NAME":
            return (_private_context(text, span.start, span.end)
                    or bool(_PRIVATE_RECORD.search(passage))
                    or _attached(_FIO_CONTEXT, text, span.start, span.end)
                    or bool(_PRIVATE_AFTER.match(text[span.end:span.end+100]))
                    or bool(re.search(r"(?i)(?:за[её]мщик\w*|пациент\w*|покупател\w*|получател\w*)\s*$", text[max(0,span.start-50):span.start]))
                    or bool(re.search(r"(?i)\b(?:утечк\w*|слив\w*|досье|резюме|crm|закрыт\w*\s+(?:баз\w*|систем\w*)|"
                                      r"личн\w*\s+(?:адрес|телефон|почт\w*)|домашн\w*\s+адрес)\b", passage)))
        return bool(_PRIVATE.search(passage))

    @staticmethod
    def _hypotheses(value: str, category: str, passage: str) -> list[str]:
        # Broad relation families select suitable NLI questions; there is no
        # allowlist of celebrity names and a bare 'public data' claim is ignored.
        if category in {"ADDRESS", "EMAIL", "PHONE"}:
            if _INSTITUTION.search(passage) or _CONTACT.search(passage):
                if category == "ADDRESS":
                    match = _INSTITUTION.search(passage)
                    if match:
                        noun = match.group().lower()
                        for stem, institution in (("музе", "музея"), ("театр", "театра"), ("библиотек", "библиотеки"), ("университет", "университета")):
                            if noun.startswith(stem):
                                return [f"В тексте указан адрес {institution}."]
                    return ["В тексте указан адрес организации."]
                return ["В тексте указаны официальные контакты организации."]
            return []
        if category in {"BIRTH_DATE", "BIRTH_PLACE", "CITIZENSHIP"}:
            if _SCIENCE.search(passage) or _CULTURE.search(passage) or _PUBLIC_EVENT.search(passage):
                return ["В тексте говорится о биографии.", "В тексте говорится об истории."]
            return []
        hypotheses = []
        if _EDUCATION.search(passage):
            hypotheses.append("Это учебный текст.")
        if _SCIENCE.search(passage):
            hypotheses.append("В тексте говорится о науке.")
            if re.search(r"(?i)\bалгоритм\w*", passage):
                hypotheses.append("В тексте говорится об алгоритме.")
        if _CULTURE.search(passage):
            hypotheses.extend(["В тексте говорится о литературе.", "В тексте говорится об искусстве.", "В тексте говорится о музыке."])
        if _PUBLIC_EVENT.search(passage):
            hypotheses.extend(["Текст сообщает о публичном событии.", "В тексте говорится о биографии.", "В тексте говорится об истории."])
        if _INSTITUTION.search(passage):
            hypotheses.append("Текст описывает организацию или общественное место.")
            if re.search(r"(?i)\bимени\b", passage):
                hypotheses.append("В тексте говорится о названии учреждения.")
        return hypotheses
