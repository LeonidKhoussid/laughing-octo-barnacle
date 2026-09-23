"""Pinned local ONNX inference. Offsets are Python Unicode [start, end) indices.

Only model objects are cached. Request text and predictions are never retained
between calls, logged, downloaded, or included in inference errors.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
from threading import Lock, BoundedSemaphore
from typing import Any

import numpy as np

from app.observability.telemetry import record_model_tokens
from app.security.limits import BackpressureError


# Suppress ORT's native telemetry uploader before importing/initializing ORT.
# disable_telemetry_events() alone stops events but can leave its HTTP worker
# alive. ORT_DISABLE_TELEMETRY prevents creating that worker at all.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

MANIFEST = Path(__file__).resolve().parents[2] / "configs" / "semantic-models.json"
NER_TOKENS = 256
NER_OVERLAP = 48
NLI_TOKENS = 512
BATCH_SIZE = 8
_MAX_NER_THREADS = 32


class ModelUnavailable(RuntimeError):
    """A safe error: never expose tokenizer/model exception input details."""

    def __init__(self) -> None:
        super().__init__("Local semantic model is unavailable.")


@dataclass(frozen=True)
class Entity:
    start: int
    end: int
    label: str
    score: float


def _ner_threads() -> int:
    """Return configured NER intra-op threads, bounded by available CPUs.

    ``PII_NER_THREADS`` accepts 1..32. The default is four threads, capped on
    smaller hosts. Context/NLI intentionally remains single-threaded so short
    request throughput is not traded for one large NER document.
    """
    raw = os.environ.get("PII_NER_THREADS", "4")
    try:
        requested = int(raw)
    except ValueError:
        raise ValueError("PII_NER_THREADS must be an integer") from None
    if not 1 <= requested <= _MAX_NER_THREADS:
        raise ValueError(f"PII_NER_THREADS must be within 1..{_MAX_NER_THREADS}")
    return min(requested, max(1, os.cpu_count() or 1))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_assets(model_dir: Path) -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for name, spec in manifest["models"].items():
        for filename, asset in spec["files"].items():
            if file_sha256(model_dir / name / filename) != asset["sha256"]:
                raise ModelUnavailable()
    return manifest


def _softmax(logits: np.ndarray) -> np.ndarray:
    if not np.isfinite(logits).all():
        raise ModelUnavailable()
    probabilities = np.exp(logits - logits.max(axis=-1, keepdims=True))
    return probabilities / probabilities.sum(axis=-1, keepdims=True)


def _run(session: Any, rows: list[tuple[list[int], list[int]]], pad: int) -> np.ndarray:
    """Dynamic padding within one bounded batch; all inputs use int64."""
    width = max(len(ids) for ids, _ in rows)
    ids = np.full((len(rows), width), pad, dtype=np.int64)
    mask = np.zeros_like(ids)
    types = np.zeros_like(ids)
    for row, (token_ids, token_types) in enumerate(rows):
        size = len(token_ids)
        ids[row, :size] = token_ids
        mask[row, :size] = 1
        types[row, :size] = token_types
    inputs = {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}
    return session.run(None, {item.name: inputs[item.name] for item in session.get_inputs()})[0]


class ModelRuntime:
    def __init__(self, model_dir: str) -> None:
        # Do not let expensive NLI occupy every request worker. Surplus context
        # work is explicitly retried, never classified by a weaker fallback.
        self._context_slots = BoundedSemaphore(min(2, max(1, os.cpu_count() or 1)))
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            # Keep the public API opt-out too, for older runtime releases.
            ort.disable_telemetry_events()
            directory = Path(model_dir)
            self.manifest = _verify_assets(directory)
            ner_options = ort.SessionOptions()
            ner_options.intra_op_num_threads = _ner_threads()
            ner_options.inter_op_num_threads = 1
            ner_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            ner_options.log_severity_level = 4
            context_options = ort.SessionOptions()
            context_options.intra_op_num_threads = 1
            context_options.inter_op_num_threads = 1
            context_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            context_options.log_severity_level = 4
            self._sessions = {}
            self._tokenizers = {}
            self._configs = {}
            for name in ("ner", "context"):
                folder = directory / name
                self._configs[name] = json.loads((folder / "config.json").read_text())
                tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
                tokenizer.no_padding()
                tokenizer.no_truncation()
                self._tokenizers[name] = tokenizer
                self._sessions[name] = ort.InferenceSession(
                    str(folder / self.manifest["models"][name]["weights"]),
                    sess_options=ner_options if name == "ner" else context_options,
                    providers=["CPUExecutionProvider"],
                )
            labels = self._configs["context"]["label2id"]
            self._nli_order = [labels[label] for label in ("entailment", "neutral", "contradiction")]
            self._ner_labels = self._configs["ner"]["id2label"]
        except Exception:
            raise ModelUnavailable() from None

    def ner(self, text: str) -> list[Entity]:
        try:
            return self._ner(text)
        except Exception:
            raise ModelUnavailable() from None

    def _ner(self, text: str) -> list[Entity]:
        if not text:
            return []
        tokenizer = self._tokenizers["ner"]
        # Tokenizer overflow behavior differs across releases. Encode once with
        # truncation disabled, then window the complete token stream explicitly.
        encoded = tokenizer.encode(text, add_special_tokens=False)
        ids, offsets = encoded.ids, encoded.offsets
        if not ids:
            return []
        capacity = NER_TOKENS - 2
        cls_id, sep_id = tokenizer.token_to_id("[CLS]"), tokenizer.token_to_id("[SEP]")
        if cls_id is None or sep_id is None:
            raise ModelUnavailable()
        windows = []
        for start in range(0, len(ids), capacity - NER_OVERLAP):
            stop = min(start + capacity, len(ids))
            windows.append((start, stop))
            if stop == len(ids):
                break
        # For overlapping windows, retain the prediction with most surrounding
        # context. Merge BIO labels globally, so a name can cross a window edge.
        selected: dict[int, tuple[int, str, float]] = {}
        for batch_start in range(0, len(windows), BATCH_SIZE):
            batch = windows[batch_start:batch_start + BATCH_SIZE]
            rows = [([cls_id, *ids[start:stop], sep_id], [0] * (stop - start + 2)) for start, stop in batch]
            logits = _run(self._sessions["ner"], rows, self._configs["ner"]["pad_token_id"])
            record_model_tokens("ner", sum(len(row[0]) for row in rows))
            probabilities = _softmax(logits)
            if probabilities.shape[:2] != (len(rows), max(len(row[0]) for row in rows)):
                raise ModelUnavailable()
            for row, (start, stop) in enumerate(batch):
                for index in range(start, stop):
                    distribution = probabilities[row, index - start + 1]
                    label_id = int(distribution.argmax())
                    rank = min(index - start if start else capacity,
                               stop - 1 - index if stop < len(ids) else capacity)
                    previous = selected.get(index)
                    if previous is None or rank > previous[0]:
                        selected[index] = (rank, self._ner_labels[str(label_id)], float(distribution[label_id]))
        if len(selected) != len(ids):
            raise ModelUnavailable()
        entities: list[Entity] = []
        active: list[Any] | None = None
        for index, (start, end) in enumerate(offsets):
            if not 0 <= start <= end <= len(text):
                raise ModelUnavailable()
            _, bio_label, score = selected[index]
            if start == end:
                continue
            prefix, _, label = bio_label.partition("-")
            if prefix not in {"B", "I"}:
                if active:
                    entities.append(Entity(active[0], active[1], active[2], sum(active[3]) / len(active[3])))
                    active = None
                continue
            if active and label == active[2] and (prefix == "I" or start == active[1]):
                active[1] = end
                active[3].append(score)
            else:
                if active:
                    entities.append(Entity(active[0], active[1], active[2], sum(active[3]) / len(active[3])))
                active = [start, end, label, [score]]
        if active:
            entities.append(Entity(active[0], active[1], active[2], sum(active[3]) / len(active[3])))
        return entities

    def nli(self, pairs: list[tuple[str, str]]) -> list[tuple[float, float, float]]:
        """Return scores in entailment, neutral, contradiction order.

        Scores are model softmax outputs, not calibrated privacy probabilities.
        Input exceeding the model's token limit raises instead of truncating.
        """
        if not pairs:
            return []
        if not self._context_slots.acquire(blocking=False):
            raise BackpressureError("context inference at capacity", reason="context")
        try:
            result = []
            tokenizer = self._tokenizers["context"]
            for start in range(0, len(pairs), BATCH_SIZE):
                encoded = [tokenizer.encode(premise, hypothesis) for premise, hypothesis in pairs[start:start + BATCH_SIZE]]
                if any(len(row.ids) > NLI_TOKENS for row in encoded):
                    raise ModelUnavailable()
                logits = _run(self._sessions["context"], [(row.ids, row.type_ids) for row in encoded], self._configs["context"]["pad_token_id"])
                record_model_tokens("context", sum(len(row.ids) for row in encoded))
                probabilities = _softmax(logits)
                if probabilities.shape != (len(encoded), 3):
                    raise ModelUnavailable()
                result.extend(tuple(float(row[index]) for index in self._nli_order) for row in probabilities)
            return result
        except Exception:
            raise ModelUnavailable() from None
        finally:
            self._context_slots.release()


@lru_cache(maxsize=2)
def _cached_runtime(model_dir: str) -> ModelRuntime:
    return ModelRuntime(model_dir)


_LOAD_LOCK = Lock()


def get_runtime(model_dir: str) -> ModelRuntime:
    # functools alone permits two simultaneous cache misses to load twice.
    # The lock is held only for lookup/loading, never request inference.
    with _LOAD_LOCK:
        return _cached_runtime(str(Path(model_dir).resolve()))
