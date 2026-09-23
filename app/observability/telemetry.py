"""Request-scoped, safe processing telemetry.

The context contains only server-generated/request-independent metadata. Bodies,
payload IDs, mappings, tokens, and credentials never enter it.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from app.observability.logs import SafeLogger
from app.observability.metrics import Metrics

_STAGES = frozenset({"detect", "resolve", "mask", "unmask", "vault", "result", "errors"})
_context: ContextVar["RequestTelemetry | None"] = ContextVar("request_telemetry", default=None)


@dataclass(frozen=True)
class RequestTelemetry:
    logger: SafeLogger
    metrics: Metrics
    request_id: str
    operation: str
    consumer: str
    policy_version: str


_result: ContextVar[str | None] = ContextVar("request_telemetry_result", default=None)


@contextmanager
def request_telemetry(
    logger: SafeLogger,
    metrics: Metrics,
    *,
    request_id: str | None = None,
    operation: str,
    consumer: str,
    policy_version: str,
) -> Iterator[str]:
    """Bind telemetry to one request and reset it when that request completes."""
    request_id = request_id or logger.new_request_id()
    token = _context.set(RequestTelemetry(
        logger=logger,
        metrics=metrics,
        request_id=request_id,
        operation=operation,
        consumer=consumer,
        policy_version=policy_version,
    ))
    result_token = _result.set(None)
    try:
        yield request_id
    except BaseException:
        if _context.get() is not None and _result.get() is None:
            record_result("error")
        raise
    else:
        if _context.get() is not None and _result.get() is None:
            record_result("success")
    finally:
        _result.reset(result_token)
        _context.reset(token)


def stage(name: str, *, detected_counts: dict[str, int] | None = None, result: str | None = None,
          overload_reason: str | None = None) -> None:
    """Emit one safe stage event when execution is inside a request context."""
    ctx = _context.get()
    if ctx is None or name not in _STAGES:
        return
    ctx.metrics.inc("pii_processing_stages_total", stage=name)
    ctx.logger.info(
        "processing stage",
        request_id=ctx.request_id,
        operation=ctx.operation,
        consumer=ctx.consumer,
        policy_version=ctx.policy_version,
        stage=name,
        detected_counts=detected_counts,
        result=result,
        overload_reason=overload_reason,
    )


def record_result(result: str, *, overload_reason: str | None = None) -> None:
    """Record a terminal result once; callers may map overloads explicitly."""
    ctx = _context.get()
    if ctx is None or _result.get() is not None:
        return
    _result.set(result)
    stage("result" if result == "success" else "errors", result=result, overload_reason=overload_reason)


def record_model_tokens(model: str, count: int) -> None:
    """Record successful local model input tokens for the current request only."""
    ctx = _context.get()
    if ctx is not None:
        ctx.metrics.record_model_tokens(model, count)
