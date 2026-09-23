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

import anyio

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
from app.observability.telemetry import request_telemetry, record_result
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
    # Completed mappings need no neural inference. Reserve bounded capacity so
    # ongoing masking cannot prevent clients from finishing their round trip.
    replay_limits = Limits(
        max_body_bytes=limits.max_body_bytes, max_in_flight=limits.max_in_flight,
        max_processing_seconds=limits.max_processing_seconds,
    )
    replay_workers = anyio.CapacityLimiter(limits.max_in_flight)

    @router.post("/process")
    async def process(req: ProcessRequest) -> ProcessResponse:
        started_at = time.monotonic()
        status = "error"
        with request_telemetry(
            logger, metrics, operation="process", consumer="autocheck",
            policy_version=policy_store.policy_version,
        ):
            try:
                policy = policy_store.get_consumer("autocheck")
                if policy is None or not policy.enabled:
                    raise processing_error()
                limits.check_body(len(req.payload.encode("utf-8")))
                try:
                    outcome = await limits.run(
                        lambda: lifecycle.process(policy.namespace, req.payload_id, req.payload, policy),
                        run_sync=run_in_threadpool,
                    )
                except BackpressureError as exc:
                    if exc.reason != "admission":
                        raise
                    outcome = await replay_limits.run(
                        lambda: lifecycle.process_existing(policy.namespace, req.payload_id, req.payload, policy),
                        run_sync=lambda callback: anyio.to_thread.run_sync(callback, limiter=replay_workers),
                    )
                    if outcome is None:
                        raise exc
                status = "success"
                return ProcessResponse(result=outcome.masked_text)
            except (BackpressureError, DeadlineExceededError) as exc:
                status = "overload"
                metrics.inc("pii_backpressure_total", reason="process")
                reason = "deadline" if isinstance(exc, DeadlineExceededError) else exc.reason
                metrics.inc("pii_process_overload_total", reason=reason)
                record_result(status, overload_reason=reason)
                raise _map_limit_error(exc)
            except BodyTooLargeError as exc:
                raise _map_limit_error(exc)
            except Exception as exc:
                raise _map_lifecycle_error(exc)
            finally:
                metrics.record_request(
                    operation="process", status=status, text_characters=len(req.payload),
                    elapsed_seconds=time.monotonic() - started_at,
                )

    return router


def _count_categories(mapping: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in mapping:
        if token.startswith("\u27e6PII:") and token.endswith("\u27e7"):
            parts = token[5:-1].split(":")
            if len(parts) >= 2:
                counts[parts[0]] = counts.get(parts[0], 0) + 1
    return counts
