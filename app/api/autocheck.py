"""POST /process endpoint — official contract (Appendix A).

Request:  {"payload": "<string>", "payload_id": "<string>"}
Response: {"result": "<string>"}

One endpoint handles both masking and demasking, correlated by payload_id.
Routing is content-based (not a blind toggle) so retries are safe:
  - New ID + original            -> mask, commit mapping before success
  - Same ID + same original      -> return the same mask (mask replay)
  - Same ID + exact returned mask -> restore the original (demask)
  - Repeated demask              -> return the same original (demask replay)
  - Conflicting input            -> safe error, no overwrite
  - Original == masked (no PII)  -> handled without a false conflict

Works locally without LLM and without auth (competition exception). Isolated
from protected namespaces: the autocheck namespace is separate, so a public
request cannot access a protected consumer's context (R37).
"""
from __future__ import annotations

import time
import threading

from fastapi import APIRouter
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.errors import (
    conflict,
    context_missing,
    integrity_error,
    overloaded_429,
    processing_error,
    retryable,
    too_large,
)
from app.core.engine import Engine
from app.nlp.model_runtime import ModelUnavailable
from app.observability.logs import SafeLogger
from app.observability.metrics import Metrics
from app.policies.loader import PolicyStore
from app.security.limits import BackpressureError, BodyTooLargeError, DeadlineExceededError, Limits
from app.vault.base import Vault
from app.vault.lifecycle import (
    ConflictError,
    ContextMissingError,
    IntegrityError,
    Lifecycle,
    RetryableError,
)

# Official contract (Appendix A): {payload, payload_id} -> {result}.
class ProcessRequest(BaseModel):
    payload_id: str = Field(..., description="Correlation key for mask->demask pair")
    payload: str = Field(..., description="Original text (mask) or returned mask (demask)")


class ProcessResponse(BaseModel):
    result: str = Field(..., description="Masked string (mask) or original string (demask)")


def _map_lifecycle_error(exc: Exception):
    if isinstance(exc, ConflictError):
        return conflict()
    if isinstance(exc, ContextMissingError):
        return context_missing()
    if isinstance(exc, (RetryableError, ModelUnavailable)):
        return retryable()
    if isinstance(exc, IntegrityError):
        return integrity_error()
    return processing_error()


def _map_limit_error(exc: Exception):
    if isinstance(exc, (BackpressureError, DeadlineExceededError)):
        return overloaded_429()
    if isinstance(exc, BodyTooLargeError):
        return too_large()
    return processing_error()


def build_autocheck_router(
    engine: Engine,
    vault: Vault,
    policy_store: PolicyStore,
    logger: SafeLogger,
    metrics: Metrics,
    lifecycle: Lifecycle | None = None,
    limits: Limits | None = None,
) -> APIRouter:
    router = APIRouter(tags=["autocheck"])
    if lifecycle is None:
        lifecycle = Lifecycle(vault, engine, None, policy_store.policy_version)  # type: ignore[arg-type]
    if limits is None:
        limits = Limits()

    @router.post("/process")
    async def process(req: ProcessRequest) -> ProcessResponse:
        policy = policy_store.get_consumer("autocheck")
        if policy is None or not policy.enabled:
            # Disabled autocheck must not process requests (D07). The official
            # competition endpoint exception applies only to authentication, not
            # to a consumer that is explicitly disabled.
            raise processing_error()
        request_id = logger.new_request_id()
        try:
            limits.check_body(len(req.payload.encode("utf-8")))
            limits.acquire()
        except (BackpressureError, BodyTooLargeError) as exc:
            metrics.inc("pii_backpressure_total", reason="process")
            raise _map_limit_error(exc)
        execution_lock = threading.Lock()
        execution_started = False
        abandoned = False

        def execute():
            nonlocal execution_started
            with execution_lock:
                if abandoned:
                    return None
                execution_started = True
            try:
                return lifecycle.process(policy.namespace, req.payload_id, req.payload, policy)
            finally:
                # A native asyncio cancellation can stop awaiting a thread but
                # cannot stop its work. Keep admission until that work finishes.
                limits.release()

        try:
            started_at = time.monotonic()
            with metrics.timed("pii_request_duration_seconds", operation="process"):
                # Admit before AnyIO's worker pool so overload cannot accumulate
                # in its queue. The deadline includes time waiting for a worker.
                outcome = await run_in_threadpool(execute)
            limits.check_deadline(started_at)
            metrics.inc("pii_requests_total", operation="process", status="success")
            logger.info(
                "process completed",
                request_id=request_id,
                operation="process",
                consumer="autocheck",
                policy_version=policy_store.policy_version,
                stage="completed",
                detected_counts=_count_categories(outcome.mapping),
                degraded=False,
                result="success",
            )
            return ProcessResponse(result=outcome.masked_text)
        except DeadlineExceededError as exc:
            # Processing-budget exhaustion is a retryable overload (429), not a
            # 422. Route it through _map_limit_error (review issue 4).
            metrics.inc("pii_requests_total", operation="process", status="overload")
            raise _map_limit_error(exc)
        except Exception as exc:
            metrics.inc("pii_requests_total", operation="process", status="error")
            raise _map_lifecycle_error(exc)
        finally:
            with execution_lock:
                if not execution_started:
                    # Cancellation while queued must free capacity and prevent
                    # a worker already scheduled by AnyIO from starting later.
                    abandoned = True
                    limits.release()

    return router


def _count_categories(mapping: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in mapping:
        if token.startswith("\u27e6PII:") and token.endswith("\u27e7"):
            parts = token[5:-1].split(":")
            if len(parts) >= 2:
                counts[parts[0]] = counts.get(parts[0], 0) + 1
    return counts
