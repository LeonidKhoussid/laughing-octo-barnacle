"""Predefined spelling variations of a labeled synthetic example.

Each mutation transforms (text, gold_spans) into (new_text, new_gold_spans)
with a reproducible seed. Gold spans are transferred by tracking how each
original character boundary maps to a position in the new text (section 16.4).

The mutations are deterministic given the seed. They cover the metamorphic
families required by section 16.4: case, NBSP/spaces, dashes/brackets/
separators, sentence/field reordering, innocent prefix/suffix, repeated value,
entity at chunk boundary, and emoji/combining marks before and inside valid
strings.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GoldSpan:
    """A labeled sensitive span in ORIGINAL coordinates."""

    start: int
    end: int
    category: str


@dataclass
class LabeledExample:
    """A synthetic example with gold spans and preserved service words."""

    text: str
    gold_spans: list[GoldSpan] = field(default_factory=list)
    service_words: list[str] = field(default_factory=list)


@dataclass
class MutationResult:
    """One transformed variation with transferred gold spans."""

    name: str
    text: str
    gold_spans: list[GoldSpan]


def _build(
    text: str,
    transform=lambda ch: ch,
    insert_before: dict[int, list[str]] | None = None,
    insert_after: dict[int, list[str]] | None = None,
) -> tuple[str, list[int]]:
    """Build a new text and a span_map.

    span_map[i] is the new-text position where original boundary i lands
    (span_map[0] == 0, span_map[len(text)] == len(new_text)). insert_before[i]
    inserts chars before original char i; insert_after[i] inserts after it.
    """
    insert_before = insert_before or {}
    insert_after = insert_after or {}
    new_parts: list[str] = []
    span_map = [0]
    for i, ch in enumerate(text):
        for ins in insert_before.get(i, []):
            new_parts.append(ins)
        new_parts.append(transform(ch))
        for ins in insert_after.get(i, []):
            new_parts.append(ins)
        span_map.append(len("".join(new_parts)))
    for ins in insert_before.get(len(text), []):
        new_parts.append(ins)
    return "".join(new_parts), span_map


def _remap(gold_spans: list[GoldSpan], span_map: list[int]) -> list[GoldSpan]:
    out: list[GoldSpan] = []
    for g in gold_spans:
        s = span_map[g.start]
        e = span_map[g.end]
        if e > s:
            out.append(GoldSpan(s, e, g.category))
    return out


def _mutate_uppercase(text: str, gold: list[GoldSpan]) -> tuple[str, list[GoldSpan]]:
    new_text, span_map = _build(text, transform=str.upper)
    return new_text, _remap(gold, span_map)


def _mutate_lowercase(text: str, gold: list[GoldSpan]) -> tuple[str, list[GoldSpan]]:
    new_text, span_map = _build(text, transform=str.lower)
    return new_text, _remap(gold, span_map)


def _mutate_nbsp_spaces(
    text: str, gold: list[GoldSpan], seed: int
) -> tuple[str, list[GoldSpan]]:
    rng = random.Random(seed)

    def transform(ch: str) -> str:
        if ch == " ":
            return "\u00a0" if rng.random() < 0.5 else "  "
        return ch

    new_text, span_map = _build(text, transform=transform)
    return new_text, _remap(gold, span_map)


def _mutate_dash_variants(
    text: str, gold: list[GoldSpan], seed: int
) -> tuple[str, list[GoldSpan]]:
    rng = random.Random(seed)
    variants = ["\u2013", "\u2014", "-", "/"]

    def transform(ch: str) -> str:
        if ch == "-":
            return rng.choice(variants)
        return ch

    new_text, span_map = _build(text, transform=transform)
    return new_text, _remap(gold, span_map)


def _mutate_bracket_wrap(
    text: str, gold: list[GoldSpan]
) -> tuple[str, list[GoldSpan]]:
    insert_before: dict[int, list[str]] = {}
    insert_after: dict[int, list[str]] = {}
    for g in gold:
        insert_before.setdefault(g.start, []).append("[")
        insert_after.setdefault(g.end, []).append("]")
    new_text, span_map = _build(text, insert_before=insert_before, insert_after=insert_after)
    return new_text, _remap(gold, span_map)


def _mutate_reorder_fields(
    text: str, gold: list[GoldSpan], seed: int
) -> tuple[str, list[GoldSpan]]:
    # Split into fields on ", ". Only reorder if every gold span lies within a
    # single field (no span crosses a delimiter). Otherwise return unchanged.
    delimiters = list(re.finditer(r", ", text))
    if not delimiters:
        return text, gold
    segments: list[tuple[str, int]] = []
    start = 0
    for m in delimiters:
        segments.append((text[start : m.start()], start))
        start = m.end()
    segments.append((text[start:], start))

    # Check every gold span is within a single segment.
    for g in gold:
        seg_idx = None
        for i, (seg, seg_start) in enumerate(segments):
            if seg_start <= g.start and g.end <= seg_start + len(seg):
                seg_idx = i
                break
        if seg_idx is None:
            return text, gold

    rng = random.Random(seed)
    order = list(range(len(segments)))
    rng.shuffle(order)

    new_parts: list[str] = []
    span_map = [0] * (len(text) + 1)
    pos = 0
    for idx, seg_idx in enumerate(order):
        seg, seg_start = segments[seg_idx]
        if idx > 0:
            new_parts.append(", ")
            pos += 2
        for j, ch in enumerate(seg):
            new_parts.append(ch)
            span_map[seg_start + j] = pos
            pos += 1
        span_map[seg_start + len(seg)] = pos
    new_text = "".join(new_parts)
    return new_text, _remap(gold, span_map)


def _mutate_prefix_suffix(
    text: str, gold: list[GoldSpan]
) -> tuple[str, list[GoldSpan]]:
    prefix = "Запрос: "
    suffix = " (обработано)"
    new_text = prefix + text + suffix
    shift = len(prefix)
    span_map = [shift + i for i in range(len(text) + 1)]
    return new_text, _remap(gold, span_map)


def _mutate_repeat_value(
    text: str, gold: list[GoldSpan]
) -> tuple[str, list[GoldSpan]]:
    sep = "\n"
    offset = len(text) + len(sep)
    new_text = text + sep + text
    span_map = list(range(len(text) + 1)) + [offset + i for i in range(len(text) + 1)]
    new_gold: list[GoldSpan] = []
    for g in gold:
        new_gold.append(GoldSpan(g.start, g.end, g.category))
        new_gold.append(GoldSpan(g.start + offset, g.end + offset, g.category))
    return new_text, new_gold


def _mutate_chunk_boundary(
    text: str, gold: list[GoldSpan]
) -> tuple[str, list[GoldSpan]]:
    if not gold:
        return text, gold
    g = gold[0]
    sep = "\n---\n"
    new_text = text[: g.start] + sep + text[g.start :]
    span_map = [i if i <= g.start else i + len(sep) for i in range(len(text) + 1)]
    return new_text, _remap(gold, span_map)


def _mutate_emoji_combining(
    text: str, gold: list[GoldSpan]
) -> tuple[str, list[GoldSpan]]:
    insert_before: dict[int, list[str]] = {}
    insert_after: dict[int, list[str]] = {}
    for g in gold:
        insert_before.setdefault(g.start, []).append("\U0001f600")  # 😀 before value
        insert_after.setdefault(g.start, []).append("\u0301")  # combining accent inside
    new_text, span_map = _build(text, insert_before=insert_before, insert_after=insert_after)
    return new_text, _remap(gold, span_map)


MUTATION_NAMES = [
    "uppercase",
    "lowercase",
    "nbsp_spaces",
    "dash_variants",
    "bracket_wrap",
    "reorder_fields",
    "prefix_suffix",
    "repeat_value",
    "chunk_boundary",
    "emoji_combining",
]


def apply_mutations(example: LabeledExample, seed: int) -> list[MutationResult]:
    """Apply all predefined mutations with a reproducible seed.

    Returns a list of MutationResult, one per mutation, in a stable order.
    """
    results: list[MutationResult] = []
    for name in MUTATION_NAMES:
        if name == "uppercase":
            new_text, new_gold = _mutate_uppercase(example.text, example.gold_spans)
        elif name == "lowercase":
            new_text, new_gold = _mutate_lowercase(example.text, example.gold_spans)
        elif name == "nbsp_spaces":
            new_text, new_gold = _mutate_nbsp_spaces(example.text, example.gold_spans, seed)
        elif name == "dash_variants":
            new_text, new_gold = _mutate_dash_variants(example.text, example.gold_spans, seed)
        elif name == "bracket_wrap":
            new_text, new_gold = _mutate_bracket_wrap(example.text, example.gold_spans)
        elif name == "reorder_fields":
            new_text, new_gold = _mutate_reorder_fields(example.text, example.gold_spans, seed)
        elif name == "prefix_suffix":
            new_text, new_gold = _mutate_prefix_suffix(example.text, example.gold_spans)
        elif name == "repeat_value":
            new_text, new_gold = _mutate_repeat_value(example.text, example.gold_spans)
        elif name == "chunk_boundary":
            new_text, new_gold = _mutate_chunk_boundary(example.text, example.gold_spans)
        elif name == "emoji_combining":
            new_text, new_gold = _mutate_emoji_combining(example.text, example.gold_spans)
        else:  # pragma: no cover
            continue
        results.append(MutationResult(name=name, text=new_text, gold_spans=new_gold))
    return results
