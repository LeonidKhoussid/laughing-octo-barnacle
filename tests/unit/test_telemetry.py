"""Safe request telemetry covers real Engine/Lifecycle stages without body logs."""
from __future__ import annotations

import io
import logging
import re
import asyncio
import json
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor

from starlette.concurrency import run_in_threadpool

from app.core.engine import Engine
from app.core.models import Decision, Detection, Span
from app.observability.logs import SafeLogger
from app.observability.metrics import Metrics
from app.observability.telemetry import record_result, request_telemetry, stage
from app.policies.schema import ConsumerPolicy
from app.vault.fingerprint import Fingerprinter
from app.vault.lifecycle import Lifecycle
from app.vault.memory import MemoryVault


class _EmailDetector:
    def detect(self, text: str) -> list[Detection]:
        start = text.index("@") - 1
        return [Detection(
            entity_id="email-1",
            category="EMAIL",
            evidence_spans=(Span(start, len(text)),),
            sensitive_spans=(Span(start, len(text)),),
            decision=Decision.MASK,
        )]


def _logger(name: str) -> tuple[SafeLogger, io.StringIO]:
    stream = io.StringIO()
    native = logging.getLogger(name)
    native.handlers = [logging.StreamHandler(stream)]
    native.setLevel(logging.INFO)
    return SafeLogger(name), stream


def _policy() -> ConsumerPolicy:
    return ConsumerPolicy(name="test", namespace="test", authentication="api_key")


def test_record_request_reconciles_failures_and_reports_estimated_token_rate():
    metrics = Metrics()
    metrics.record_request(operation="mask", status="success", text_characters=5, elapsed_seconds=0.01)
    metrics.record_request(operation="mask", status="error", text_characters=8, elapsed_seconds=0.02)
    metrics.record_request(operation="unmask", status="overload", text_characters=0, elapsed_seconds=0.03)

    snapshot = metrics.snapshot()
    counters = snapshot["counters"]
    assert sum(value for key, value in counters.items() if key.startswith("pii_requests_total/")) == 3
    assert counters["pii_input_tokens_estimated_total/operation=mask"] == 4
    traffic = snapshot["traffic"]
    assert traffic["request_count"] == 3
    assert traffic["input_tokens_estimated"] == 4
    assert traffic["input_token_method"] == "estimated_ceil_characters_div_4"
    assert traffic["token_count_unit"] == "estimated_input_tokens"
    assert traffic["scope"] == "worker_local_in_process"


def test_rolling_rates_separate_rejections_operations_and_success_latency(monkeypatch):
    from app.observability import metrics as module
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    metrics = Metrics()
    clock[0] = 2.0
    for status, count, duration in (("success", 30, .1), ("overload", 70, .0001), ("error", 1, .002)):
        for _ in range(count):
            metrics.record_request(operation="process", status=status,
                                   text_characters=8, elapsed_seconds=duration)
    metrics.record_request(operation="mask", status="success", text_characters=4, elapsed_seconds=.2)
    snapshot = metrics.snapshot()
    traffic = snapshot["traffic"]
    process = traffic["by_operation"]["process"]
    assert traffic["request_count"] == 102
    assert process["request_count"] == 101
    assert process["request_rate_per_second"] == 50.5
    assert process["successful_requests_per_second"] == 15
    assert process["success_fraction"] == 30 / 101
    assert process["by_status"]["overload"] == {"request_count": 70, "request_rate_per_second": 35}
    assert sum(outcome["request_count"] for outcome in process["by_status"].values()) == 101
    success = snapshot["histograms"]["pii_request_outcome_duration_seconds/operation=process/status=success"]
    assert success["count"] == 30 and success["p95"] == .1
    assert "All completed" in traffic["input_token_scope"]
    clock[0] = 63.0
    expired = metrics.traffic_snapshot()["by_operation"]["process"]
    assert expired["request_count"] == expired["successful_requests_per_second"] == 0
    assert expired["success_fraction"] is None
    assert metrics.snapshot()["counters"]["pii_requests_total/operation=process/status=success"] == 30


def test_rotating_file_keeps_safe_events_without_duplicate_journal_output(tmp_path, caplog):
    path = tmp_path / "gateway.log"
    name = "telemetry-file-test"
    logger = SafeLogger(name, log_file=str(path))
    second = SafeLogger(name, log_file=str(path))
    native = logging.getLogger(name)
    try:
        assert len(native.handlers) == 1 and not native.propagate
        handler = native.handlers[0]
        assert isinstance(handler, RotatingFileHandler)
        assert handler.maxBytes == 100 * 1024 * 1024 and handler.backupCount == 5
        handler.maxBytes = 1024  # Exercise actual rotation without a large fixture.
        handler.backupCount = 20
        for i in range(60):
            (logger if i % 2 else second).info("processing stage", request_id=str(i),
                stage="errors", result="overload", overload_reason="admission",
                body="PRIVATE-CANARY", detected_counts={"EMAIL": 1, "PRIVATE-CANARY": 1})
        files = list(tmp_path.glob("gateway.log*"))
        assert len(files) > 1
        lines = [line for file in files for line in file.read_text().splitlines()]
        records = [json.loads(line) for line in lines]
        assert len(records) == 60
        assert {record["request_id"] for record in records} == {str(i) for i in range(60)}
        assert all(record["overload_reason"] == "admission" for record in records)
        assert "PRIVATE-CANARY" not in "".join(lines)
        assert not [record for record in caplog.records if record.name == name]
    finally:
        for handler in native.handlers[:]:
            native.removeHandler(handler)
            handler.close()
        native.propagate = True


def test_tokens_never_become_metric_labels_or_log_fields():
    logger, stream = _logger("telemetry-token-canary")
    metrics = Metrics()
    token_canary = "⟦PII:EMAIL:never-log-this⟧"
    metrics.inc("pii_processing_stages_total", stage="mask", token=token_canary)
    with request_telemetry(
        logger, metrics, operation="mask", consumer="test", policy_version="v1",
    ) as request_id:
        stage("mask", detected_counts={"EMAIL": 1})

    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert token_canary not in str(metrics.snapshot())
    assert token_canary not in stream.getvalue()


def test_request_context_is_thread_local_and_log_canary_safe():
    logger, stream = _logger("telemetry-thread-local")
    metrics = Metrics()
    canary = "raw@example.test|token|payload-id|credential"

    def emit(request_id: str) -> None:
        with request_telemetry(
            logger, metrics, request_id=request_id, operation="mask",
            consumer="test", policy_version="v1",
        ):
            stage("detect", detected_counts={"EMAIL": 1, canary: 9})

    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(emit, ("request-a", "request-b")))

    output = stream.getvalue()
    assert canary not in output
    assert output.count('"request_id": "request-a"') == 2
    assert output.count('"request_id": "request-b"') == 2
    assert output.count('"stage": "detect"') == 2
    assert output.count('"stage": "result"') == 2


def test_request_context_reaches_api_worker_thread():
    logger, stream = _logger("telemetry-api-worker")
    metrics = Metrics()

    async def exercise() -> None:
        with request_telemetry(
            logger, metrics, request_id="worker-request", operation="process",
            consumer="test", policy_version="v1",
        ):
            await run_in_threadpool(stage, "vault")

    asyncio.run(exercise())
    output = stream.getvalue()
    assert '"request_id": "worker-request"' in output
    assert '"stage": "vault"' in output


def test_engine_and_lifecycle_emit_real_stages_without_text_or_tokens():
    logger, stream = _logger("telemetry-real-stages")
    metrics = Metrics()
    lifecycle = Lifecycle(
        MemoryVault(), Engine([_EmailDetector()]),
        Fingerprinter(b"test-fp-key-32-bytes-long!!"), "v1",
    )
    original = "x@private.test"

    with request_telemetry(
        logger, metrics, request_id="mask-request", operation="mask",
        consumer="test", policy_version="v1",
    ):
        outcome = lifecycle.mask("test", "payload-id", original, _policy())
    with request_telemetry(
        logger, metrics, request_id="unmask-request", operation="unmask",
        consumer="test", policy_version="v1",
    ):
        assert lifecycle.unmask("test", "payload-id", outcome.masked_text, _policy()) == original

    output = stream.getvalue()
    for stage_name in ("vault", "detect", "resolve", "mask", "unmask", "result"):
        assert f'"stage": "{stage_name}"' in output
    assert '"detected_counts": {"EMAIL": 1}' in output
    assert original not in output
    assert outcome.masked_text not in output
    assert "payload-id" not in output
    assert metrics.snapshot()["counters"]["pii_processing_stages_total/stage=vault"] == 2


def test_explicit_overload_result_is_not_replaced_on_context_exit():
    logger, stream = _logger("telemetry-result")
    metrics = Metrics()
    with request_telemetry(
        logger, metrics, request_id="overloaded", operation="process",
        consumer="test", policy_version="v1",
    ):
        record_result("overload")
    output = stream.getvalue()
    assert '"stage": "errors"' in output
    assert '"result": "overload"' in output
    assert '"result": "success"' not in output
