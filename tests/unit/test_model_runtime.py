"""Inference plumbing checks, independent of privacy-policy thresholds."""
import json
import os
import hashlib
from io import BytesIO
from pathlib import Path
import re
from types import SimpleNamespace
import subprocess
import sys

import pytest

np = pytest.importorskip("numpy")
from app.nlp import model_runtime as runtime


class FakeTokenizer:
    def __init__(self):
        self.labels = {}

    def token_to_id(self, token):
        return {"[CLS]": 2, "[SEP]": 3}[token]

    def encode(self, text, pair=None, **kwargs):
        matches = list(re.finditer(r"\S+", text + (" " + pair if pair else "")))
        self.labels = {index + 20: {"Александр": 9, "Сергеевич": 10, "Пушкин": 10}.get(match.group(), 0)
                       for index, match in enumerate(matches)}
        return SimpleNamespace(ids=list(self.labels), type_ids=[0] * len(matches),
                               offsets=[match.span() for match in matches])


class FakeSession:
    def __init__(self, tokenizer, *, nli=False):
        self.tokenizer = tokenizer
        self.nli = nli
        self.shapes = []
        self.seen = set()

    def get_inputs(self):
        return [SimpleNamespace(name=name) for name in ("input_ids", "attention_mask", "token_type_ids")]

    def run(self, outputs, inputs):
        ids = inputs["input_ids"]
        self.shapes.append(ids.shape)
        self.seen.update(int(token) for token in ids.flat)
        if self.nli:
            return [np.tile([0.0, 1.0, 4.0], (len(ids), 1))]
        logits = np.full((*ids.shape, 11), -10.0)
        for row in range(len(ids)):
            for column, token in enumerate(ids[row]):
                logits[row, column, self.tokenizer.labels.get(int(token), 0)] = 10.0
        return [logits]


def fake_runtime():
    instance = runtime.ModelRuntime.__new__(runtime.ModelRuntime)
    tokenizers = {name: FakeTokenizer() for name in ("ner", "context")}
    instance._tokenizers = tokenizers
    instance._sessions = {name: FakeSession(tokenizer, nli=name == "context") for name, tokenizer in tokenizers.items()}
    instance._configs = {name: {"pad_token_id": 0} for name in tokenizers}
    instance._ner_labels = {str(index): "O" for index in range(11)} | {"9": "B-PER", "10": "I-PER"}
    instance._nli_order = [2, 0, 1]  # Test explicit label ordering, not index assumptions.
    return instance


def test_entire_document_and_cross_window_name_with_unicode_offsets():
    instance = fake_runtime()
    name = "Александр Сергеевич Пушкин"
    text = "😀 " + "текст " * 252 + name + " " + "текст " * 2500 + name
    result = instance.ner(text)
    assert [(entity.start, entity.end, entity.label) for entity in result] == [
        (text.index(name), text.index(name) + len(name), "PER"),
        (text.rindex(name), len(text), "PER"),
    ]
    session = instance._sessions["ner"]
    assert all(rows <= runtime.BATCH_SIZE and width <= runtime.NER_TOKENS for rows, width in session.shapes)
    assert len(session.shapes) >= 2
    assert set(instance._tokenizers["ner"].labels) <= session.seen


def test_nli_label_order_and_bounded_batches():
    instance = fake_runtime()
    scores = instance.nli([("Синтетический пример", "Гипотеза")] * 17)
    assert len(scores) == 17
    assert all(entailment > neutral and entailment > contradiction and sum(score) == pytest.approx(1)
               for score in scores for entailment, neutral, contradiction in [score])
    assert [rows for rows, _ in instance._sessions["context"].shapes] == [8, 8, 1]


def test_nli_rejects_overlong_text_without_silent_truncation():
    instance = fake_runtime()
    with pytest.raises(runtime.ModelUnavailable, match="^Local semantic model is unavailable\\.$"):
        instance.nli([("данные " * 513, "Гипотеза")])
    assert instance._sessions["context"].shapes == []


def test_runtime_error_does_not_expose_input_or_internal_details():
    instance = fake_runtime()
    def fail(*args, **kwargs):
        raise ValueError("PRIVATE-CANARY: broken tokenizer")
    instance._tokenizers["ner"].encode = fail
    with pytest.raises(runtime.ModelUnavailable) as error:
        instance.ner("PRIVATE-CANARY")
    assert "PRIVATE-CANARY" not in str(error.value)
    assert error.value.__suppress_context__ is True


def test_ner_thread_setting_defaults_to_four_and_caps_to_available_cpu(monkeypatch):
    monkeypatch.setattr(runtime.os, "cpu_count", lambda: 2)
    monkeypatch.delenv("PII_NER_THREADS", raising=False)
    assert runtime._ner_threads() == 2
    monkeypatch.setenv("PII_NER_THREADS", "1")
    assert runtime._ner_threads() == 1
    monkeypatch.setenv("PII_NER_THREADS", "4")
    assert runtime._ner_threads() == 2
    monkeypatch.setenv("PII_NER_THREADS", "0")
    with pytest.raises(ValueError, match="1..32"):
        runtime._ner_threads()


def test_missing_and_corrupt_assets_fail_verification(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"models": {"ner": {"files": {"model.onnx": {"sha256": "0" * 64}}}}}))
    monkeypatch.setattr(runtime, "MANIFEST", manifest)
    with pytest.raises((runtime.ModelUnavailable, FileNotFoundError)):
        runtime._verify_assets(tmp_path)
    (tmp_path / "ner").mkdir()
    (tmp_path / "ner" / "model.onnx").write_bytes(b"corrupt")
    with pytest.raises(runtime.ModelUnavailable):
        runtime._verify_assets(tmp_path)


def test_missing_runtime_has_safe_error(tmp_path):
    with pytest.raises(runtime.ModelUnavailable, match="^Local semantic model is unavailable\\.$"):
        runtime.ModelRuntime(str(tmp_path))


def test_telemetry_disabled_before_creating_native_session(tmp_path, monkeypatch):
    events = []
    def session(*args, **kwargs):
        events.append("session")
        assert events[0] == "telemetry_disabled"
        raise RuntimeError("Stop after checking initialization order")
    ort = SimpleNamespace(
        disable_telemetry_events=lambda: events.append("telemetry_disabled"),
        SessionOptions=SimpleNamespace,
        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL=0),
        InferenceSession=session,
    )
    tokenizer = SimpleNamespace(no_padding=lambda: None, no_truncation=lambda: None)
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "tokenizers", SimpleNamespace(
        Tokenizer=SimpleNamespace(from_file=lambda path: tokenizer)))
    monkeypatch.setattr(runtime, "_verify_assets", lambda path: {
        "models": {"ner": {"weights": "model.onnx"}}})
    (tmp_path / "ner").mkdir()
    (tmp_path / "ner" / "config.json").write_text("{}")
    with pytest.raises(runtime.ModelUnavailable):
        runtime.ModelRuntime(str(tmp_path))
    assert events == ["telemetry_disabled", "session"]


def test_nonfinite_inference_fails_closed():
    instance = fake_runtime()
    instance._sessions["context"].run = lambda *args: [np.array([[np.nan, 0, 1]])]
    with pytest.raises(runtime.ModelUnavailable):
        instance.nli([("Текст", "Гипотеза")])


def test_preparation_reuses_verified_files_without_network(tmp_path, monkeypatch):
    from scripts import download_semantic_models as download
    (tmp_path / "configs").mkdir()
    target = tmp_path / "models" / "ner" / "model.onnx"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"verified")
    asset = {"url": "https://example.invalid/pinned", "bytes": 8,
             "sha256": hashlib.sha256(b"verified").hexdigest()}
    (tmp_path / "configs" / "semantic-models.json").write_text(
        json.dumps({"models": {"ner": {"files": {"model.onnx": asset}}}}))
    monkeypatch.setattr(download, "ROOT", tmp_path)
    def no_network(*args, **kwargs):
        pytest.fail("Verified models must not be downloaded again")
    monkeypatch.setattr(download, "urlopen", no_network)
    download.prepare(tmp_path / "models")


def test_bad_download_cannot_replace_existing_asset(tmp_path, monkeypatch):
    from scripts import download_semantic_models as download
    (tmp_path / "configs").mkdir()
    target = tmp_path / "models" / "ner" / "model.onnx"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"previous")
    asset = {"url": "https://example.invalid/pinned", "bytes": 8,
             "sha256": hashlib.sha256(b"expected").hexdigest()}
    (tmp_path / "configs" / "semantic-models.json").write_text(
        json.dumps({"models": {"ner": {"files": {"model.onnx": asset}}}}))
    monkeypatch.setattr(download, "ROOT", tmp_path)
    monkeypatch.setattr(download, "urlopen", lambda *args, **kwargs: BytesIO(b"tampered"))
    with pytest.raises(RuntimeError, match="checksum"):
        download.prepare(tmp_path / "models")
    assert target.read_bytes() == b"previous"
    assert list(target.parent.glob(".download-*")) == []


@pytest.fixture(scope="module")
def real_runtime():
    pytest.importorskip("onnxruntime")
    pytest.importorskip("tokenizers")
    directory = Path(__file__).resolve().parents[2] / "models" / "semantic"
    manifest = json.loads(runtime.MANIFEST.read_text())
    if not all((directory / name / filename).is_file()
               for name, spec in manifest["models"].items() for filename in spec["files"]):
        pytest.skip("Pinned semantic models are not installed; run download_semantic_models.py")
    return runtime.get_runtime(str(directory))


def test_real_model_cpu_provider_and_nli_sanity(real_runtime):
    assert all(session.get_providers() == ["CPUExecutionProvider"] for session in real_runtime._sessions.values())
    scores = real_runtime.nli([("A man is eating food.", "A man is eating food."),
                              ("A man is eating food.", "Nobody is eating.")])
    assert scores[0][0] > .8
    assert scores[1][2] > .8


def test_real_ner_unicode_full_name_and_document_tail(real_runtime):
    name = "Александр Сергеевич Пушкин"
    text = "😀 " + "текст " * 252 + name + ". " + "текст " * 600 + "Клиент " + name + ", телефон неизвестен."
    entities = [entity for entity in real_runtime.ner(text) if entity.label == "PER"]
    for start in [text.index(name), text.rindex(name)]:
        assert any(entity.start <= start and entity.end >= start + len(name) for entity in entities)
    assert all(0 <= entity.start < entity.end <= len(text) for entity in entities)


def test_real_native_sessions_exit_normally(real_runtime):
    root = Path(__file__).resolve().parents[2]
    source = """
import os, sys
assert 'onnxruntime' not in sys.modules
from app.nlp.model_runtime import get_runtime
assert os.environ['ORT_DISABLE_TELEMETRY'] == '1'
runtime = get_runtime('models/semantic')
assert runtime.ner('Клиент Александр Сергеевич Пушкин.')
assert runtime.nli([('A man is eating food.', 'A man is eating food.')])[0][0] > .8
print('NATIVE_EXIT_CHECK_OK')
"""
    completed = subprocess.run([sys.executable, "-c", source], cwd=root,
                               capture_output=True, text=True, timeout=60,
                               env={**os.environ, "ORT_DISABLE_TELEMETRY": "0"})
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "NATIVE_EXIT_CHECK_OK"
