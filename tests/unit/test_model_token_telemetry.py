"""Actual model token telemetry uses the already-built ONNX attention masks."""
from __future__ import annotations

from types import SimpleNamespace
from threading import BoundedSemaphore

import numpy as np
import pytest

from app.nlp import model_runtime as runtime
from app.observability.logs import SafeLogger
from app.observability.metrics import Metrics
from app.observability.telemetry import request_telemetry


class _Tokenizer:
    def token_to_id(self, token: str) -> int:
        return {"[CLS]": 1, "[SEP]": 2}[token]

    def encode(self, text: str, pair: str | None = None, **kwargs):
        if pair is not None:
            return SimpleNamespace(ids=[10, 11, 12], type_ids=[0, 0, 1], offsets=[])
        ids = list(range(10, 10 + len(text)))
        return SimpleNamespace(ids=ids, type_ids=[0] * len(ids), offsets=[(i, i + 1) for i in range(len(ids))])


class _Session:
    def __init__(self, *, nli: bool = False, fail: bool = False) -> None:
        self.nli = nli
        self.fail = fail
        self.attention_token_counts: list[int] = []

    def get_inputs(self):
        return [SimpleNamespace(name=name) for name in ("input_ids", "attention_mask", "token_type_ids")]

    def run(self, outputs, inputs):
        if self.fail:
            raise RuntimeError("synthetic inference failure")
        ids = inputs["input_ids"]
        self.attention_token_counts.append(int(inputs["attention_mask"].sum()))
        if self.nli:
            return [np.tile([0.0, 1.0, 2.0], (len(ids), 1))]
        return [np.zeros((*ids.shape, 1), dtype=np.float64)]


def _runtime(ner: _Session, context: _Session):
    instance = runtime.ModelRuntime.__new__(runtime.ModelRuntime)
    instance._context_slots = BoundedSemaphore(2)
    instance._tokenizers = {"ner": _Tokenizer(), "context": _Tokenizer()}
    instance._sessions = {"ner": ner, "context": context}
    instance._configs = {"ner": {"pad_token_id": 0}, "context": {"pad_token_id": 0}}
    instance._ner_labels = {"0": "O"}
    instance._nli_order = [0, 1, 2]
    return instance


def test_actual_model_token_counts_match_feed_masks_include_overlap_and_skip_failed_runs():
    ner = _Session()
    context = _Session(nli=True)
    instance = _runtime(ner, context)
    metrics = Metrics()
    pairs = [("premise", "hypothesis"), ("another", "pair")]

    with request_telemetry(
        SafeLogger("model-token-telemetry"), metrics,
        operation="mask", consumer="test", policy_version="v1",
    ):
        instance.ner("x" * 300)  # Two overlapping NER windows.
        instance.nli(pairs)
        instance.nli(pairs)  # Repeated inference adds another exact feed count.

    counters = metrics.snapshot()["counters"]
    assert counters["pii_model_input_tokens_total/model=ner"] == sum(ner.attention_token_counts)
    assert counters["pii_model_input_tokens_total/model=context"] == sum(context.attention_token_counts)
    assert counters["pii_model_input_tokens_total/model=ner"] > 300  # CLS/SEP + overlap.
    traffic = metrics.snapshot()["model_token_traffic"]
    assert traffic["models"]["ner"]["input_tokens"] == sum(ner.attention_token_counts)
    assert traffic["models"]["context"]["input_tokens"] == sum(context.attention_token_counts)
    model_counters = {
        key: value for key, value in counters.items()
        if key.startswith("pii_model_input_tokens_total/")
    }

    failed = _runtime(_Session(fail=True), _Session(nli=True))
    with request_telemetry(
        SafeLogger("model-token-failure"), metrics,
        operation="mask", consumer="test", policy_version="v1",
    ):
        with pytest.raises(runtime.ModelUnavailable):
            failed.ner("x" * 8)
    assert {
        key: value for key, value in metrics.snapshot()["counters"].items()
        if key.startswith("pii_model_input_tokens_total/")
    } == model_counters
    durations = metrics.snapshot()["histograms"]
    # One NER batch succeeds, a second fails; both consume worker time.
    assert durations["pii_model_inference_duration_seconds/model=ner"]["count"] == 2
    assert durations["pii_model_inference_duration_seconds/model=context"]["count"] == 2
    assert "synthetic inference failure" not in str(metrics.snapshot())
