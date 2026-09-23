"""Protected demo endpoints: POST /demo/mask, /demo/unmask, /demo/chat.

Require API key for non-autocheck consumers. Uses the stub provider for chat.
Per-consumer settings (detect_types, allow_unmask, allow_llm_egress) are enforced
here. allow_unmask is re-checked at every unmask, so revoking access affects
previously-created contexts (R39).
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Header, HTTPException
from starlette.concurrency import run_in_threadpool

from app.api.errors import (
    conflict,
    context_missing,
    forbidden,
    integrity_error,
    overloaded,
    processing_error,
    retryable,
    too_large,
    unauthorized,
    unmask_denied,
)
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    MaskRequest,
    MaskResponse,
    RestoreResponseRequest,
    RestoreResponseResponse,
    SpanInfo,
    UnmaskRequest,
    UnmaskResponse,
)
from app.core.engine import Engine
from app.nlp.model_runtime import ModelUnavailable
from app.observability.logs import SafeLogger
from app.observability.metrics import Metrics
from app.observability.telemetry import record_result, request_telemetry
from app.policies.loader import PolicyStore
from app.providers.stub import StubProvider
from app.security.auth import verify_api_key
from app.security.egress import EgressGuard
from app.security.limits import BackpressureError, BodyTooLargeError, DeadlineExceededError, Limits
from app.vault.base import Vault
from app.vault.lifecycle import (
    ConflictError,
    ContextMissingError,
    IntegrityError,
    Lifecycle,
    RetryableError,
)

router = APIRouter(prefix="/demo", tags=["demo"])


def _map_lifecycle_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ConflictError):
        return conflict()
    if isinstance(exc, ContextMissingError):
        return context_missing()
    if isinstance(exc, (RetryableError, ModelUnavailable)):
        return retryable()
    if isinstance(exc, IntegrityError):
        return integrity_error()
    return processing_error()


def _map_limit_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (BackpressureError, DeadlineExceededError)):
        return overloaded()
    if isinstance(exc, BodyTooLargeError):
        return too_large()
    return processing_error()


def _protect_provider_output(engine: Engine, response: str, mapping: dict[str, str]) -> str:
    """Mask NEWLY introduced sensitive content in a provider response.

    Known tokens from the authorized context are preserved (replaced with a
    unique random placeholder before detection so they are not re-detected as
    sensitive). Any new sensitive text the provider introduced is masked. This
    is the documented output protection (section 13, R47): required protection
    failures must not release unchecked content.
    """
    if not response:
        return response
    # Replace each known token with a UNIQUE random placeholder that no detector
    # matches and that cannot collide with provider text. The placeholder uses
    # only letters (no digits) so no numeric detector (INN/CARD/PHONE) can match
    # part of it and split it (review issue 7).
    placeholders: dict[str, str] = {}
    protected = response
    for token in mapping:
        if token in protected:
            ph = "__ALFAGENPH" + uuid.uuid4().hex[:16].translate(str.maketrans("0123456789", "abcdefghij")) + "__"
            protected = protected.replace(token, ph)
            placeholders[ph] = token
    # Detect new sensitive content in the placeholder-replaced text.
    result = engine.mask(protected, "output-guard", "output-guard")
    if result.resolved_spans:
        # Mask the newly introduced sensitive spans.
        protected = result.masked_text
    # Restore the known tokens (unique placeholder -> original token).
    for ph, token in placeholders.items():
        if ph in protected:
            protected = protected.replace(ph, token)
    return protected


def build_demo_router(
    engine: Engine,
    vault: Vault,
    policy_store: PolicyStore,
    logger: SafeLogger,
    metrics: Metrics,
    limits: Limits,
    egress_guard: EgressGuard,
    stub: StubProvider,
    lifecycle: Lifecycle | None = None,
) -> APIRouter:
    r = APIRouter(prefix="/demo", tags=["demo"])
    if lifecycle is None:
        lifecycle = Lifecycle(vault, engine, None, policy_store.policy_version)  # type: ignore[arg-type]

    @r.post("/mask")
    async def mask(req: MaskRequest, x_api_key: str | None = Header(default=None)) -> MaskResponse:
        policy = policy_store.get_consumer(req.consumer)
        consumer = policy.name if policy is not None and policy.enabled else "unauthenticated"
        request_id = logger.new_request_id()
        status = "error"
        started_at = time.monotonic()
        try:
            with request_telemetry(
                logger, metrics,
                request_id=request_id,
                operation="mask",
                consumer=consumer,
                policy_version=policy_store.policy_version,
            ):
                if policy is None or not policy.enabled:
                    raise forbidden()
                if not verify_api_key(policy, x_api_key):
                    raise unauthorized()
                try:
                    limits.check_body(len(req.text.encode("utf-8")))
                except BodyTooLargeError as exc:
                    metrics.inc("pii_backpressure_total", reason="mask")
                    raise _map_limit_error(exc)
                context_id = req.context_id or uuid.uuid4().hex

                def execute():
                    return lifecycle.mask(policy.namespace, context_id, req.text, policy)

                try:
                    outcome = await limits.run(execute, run_sync=run_in_threadpool)
                except (BackpressureError, DeadlineExceededError) as exc:
                    status = "overload"
                    metrics.inc("pii_backpressure_total", reason="mask")
                    record_result(status)
                    raise _map_limit_error(exc)
                status = "success"
                return MaskResponse(
                    masked_text=outcome.masked_text,
                    policy_version=policy_store.policy_version,
                    detected_counts=_count_categories(outcome.resolved_spans),
                    context_id=context_id,
                    mask_action=policy.default_action,
                    spans=_spans_from_resolved(outcome.resolved_spans),
                    egress_disabled=not policy.allow_llm_egress,
                )
        except HTTPException:
            record_result(status)
            raise
        except (BackpressureError, DeadlineExceededError) as exc:
            status = "overload"
            metrics.inc("pii_backpressure_total", reason="mask")
            record_result(status)
            raise _map_limit_error(exc)
        except Exception as exc:
            record_result(status)
            raise _map_lifecycle_error(exc)
        finally:
            metrics.record_request(
                operation="mask", status=status, text_characters=len(req.text),
                elapsed_seconds=time.monotonic() - started_at,
            )

    @r.post("/unmask")
    async def unmask(req: UnmaskRequest, x_api_key: str | None = Header(default=None)) -> UnmaskResponse:
        policy = policy_store.get_consumer(req.consumer)
        consumer = policy.name if policy is not None and policy.enabled else "unauthenticated"
        request_id = logger.new_request_id()
        status = "error"
        started_at = time.monotonic()
        try:
            with request_telemetry(
                logger, metrics,
                request_id=request_id,
                operation="unmask",
                consumer=consumer,
                policy_version=policy_store.policy_version,
            ):
                if policy is None or not policy.enabled:
                    raise forbidden()
                if not verify_api_key(policy, x_api_key):
                    raise unauthorized()
                if not policy.allow_unmask:
                    raise unmask_denied()
                try:
                    limits.check_body(len(req.masked_text.encode("utf-8")))
                except BodyTooLargeError as exc:
                    metrics.inc("pii_backpressure_total", reason="unmask")
                    raise _map_limit_error(exc)

                def execute():
                    return lifecycle.unmask(policy.namespace, req.context_id, req.masked_text, policy)

                try:
                    original = await limits.run(execute, run_sync=run_in_threadpool)
                except (BackpressureError, DeadlineExceededError) as exc:
                    status = "overload"
                    metrics.inc("pii_backpressure_total", reason="unmask")
                    record_result(status)
                    raise _map_limit_error(exc)
                status = "success"
                return UnmaskResponse(
                    original_text=original,
                    policy_version=policy_store.policy_version,
                )
        except HTTPException:
            record_result(status)
            raise
        except (BackpressureError, DeadlineExceededError) as exc:
            status = "overload"
            metrics.inc("pii_backpressure_total", reason="unmask")
            record_result(status)
            raise _map_limit_error(exc)
        except Exception as exc:
            record_result(status)
            raise _map_lifecycle_error(exc)
        finally:
            metrics.record_request(
                operation="unmask", status=status, text_characters=len(req.masked_text),
                elapsed_seconds=time.monotonic() - started_at,
            )

    @r.post("/chat")
    async def chat(req: ChatRequest, x_api_key: str | None = Header(default=None)) -> ChatResponse:
        policy = policy_store.get_consumer(req.consumer)
        consumer = policy.name if policy is not None and policy.enabled else "unauthenticated"
        request_id = logger.new_request_id()
        status = "error"
        started_at = time.monotonic()
        try:
            with request_telemetry(
                logger, metrics,
                request_id=request_id,
                operation="chat",
                consumer=consumer,
                policy_version=policy_store.policy_version,
            ):
                if policy is None or not policy.enabled:
                    raise forbidden()
                if not verify_api_key(policy, x_api_key):
                    raise unauthorized()
                if not policy.allow_llm_egress:
                    raise forbidden()
                try:
                    limits.check_body(len(req.masked_text.encode("utf-8")))
                except BodyTooLargeError as exc:
                    metrics.inc("pii_backpressure_total", reason="chat")
                    raise _map_limit_error(exc)

                def execute():
                    # Egress must depend on ACTUAL server-side processing, not caller
                    # claims (C). Verify the masked text matches a committed record.
                    try:
                        record = vault.get(policy.namespace, req.context_id)
                    except Exception:  # noqa: BLE001
                        record = None
                    if record is None or record.get("state") not in ("READY", "RESTORED"):
                        raise context_missing()
                    if record.get("masked_text") != req.masked_text:
                        raise conflict()
                    mapping = record.get("mapping") or {}
                    check = egress_guard.check(
                        policy, stub.provider_id, req.masked_text,
                        stages_completed=["detect", "resolve", "mask"],
                        remaining_sensitive_spans=0, mapping=mapping,
                    )
                    if not check.ok:
                        metrics.inc("pii_egress_blocks_total", reason="policy")
                        raise forbidden()
                    response = stub.complete(req.masked_text)
                    protected = _protect_provider_output(engine, response, mapping)
                    metrics.inc("pii_upstream_calls_total", provider="stub", result="success")
                    return ChatResponse(response=protected, provider=stub.provider_id, stub=True)

                try:
                    response = await limits.run(execute, run_sync=run_in_threadpool)
                except (BackpressureError, DeadlineExceededError) as exc:
                    status = "overload"
                    metrics.inc("pii_backpressure_total", reason="chat")
                    record_result(status)
                    raise _map_limit_error(exc)
                status = "success"
                return response
        except HTTPException:
            record_result(status)
            raise
        except (BackpressureError, DeadlineExceededError) as exc:
            status = "overload"
            metrics.inc("pii_backpressure_total", reason="chat")
            record_result(status)
            raise _map_limit_error(exc)
        except Exception as exc:
            record_result(status)
            raise _map_lifecycle_error(exc)
        finally:
            metrics.record_request(
                operation="chat", status=status, text_characters=len(req.masked_text),
                elapsed_seconds=time.monotonic() - started_at,
            )

    @r.post("/restore-response")
    async def restore_response(
        req: RestoreResponseRequest, x_api_key: str | None = Header(default=None)
    ) -> RestoreResponseResponse:
        """Restore authorized tokens inside a changed provider response.

        The provider may prefix/reword its output, so the response is not expected
        to equal the stored masked_text. This performs EXACT token substitution
        (R25), never fuzzy/recursive/LLM-based demasking.
        """
        policy = policy_store.get_consumer(req.consumer)
        consumer = policy.name if policy is not None and policy.enabled else "unauthenticated"
        request_id = logger.new_request_id()
        status = "error"
        started_at = time.monotonic()
        try:
            with request_telemetry(
                logger, metrics,
                request_id=request_id,
                operation="restore_response",
                consumer=consumer,
                policy_version=policy_store.policy_version,
            ):
                if policy is None or not policy.enabled:
                    raise forbidden()
                if not verify_api_key(policy, x_api_key):
                    raise unauthorized()
                if not policy.allow_unmask:
                    raise unmask_denied()
                try:
                    limits.check_body(len(req.response_text.encode("utf-8")))
                except BodyTooLargeError as exc:
                    metrics.inc("pii_backpressure_total", reason="restore-response")
                    raise _map_limit_error(exc)

                def execute():
                    try:
                        record = vault.get(policy.namespace, req.context_id)
                    except Exception:  # noqa: BLE001
                        record = None
                    mapping = (record or {}).get("mapping") or {}
                    protected = _protect_provider_output(engine, req.response_text, mapping)
                    restored = lifecycle.restore_tokens(
                        policy.namespace, req.context_id, protected, policy
                    )
                    return RestoreResponseResponse(
                        restored_text=restored,
                        policy_version=policy_store.policy_version,
                    )

                try:
                    response = await limits.run(execute, run_sync=run_in_threadpool)
                except (BackpressureError, DeadlineExceededError) as exc:
                    status = "overload"
                    metrics.inc("pii_backpressure_total", reason="restore-response")
                    record_result(status)
                    raise _map_limit_error(exc)
                status = "success"
                return response
        except HTTPException:
            record_result(status)
            raise
        except (BackpressureError, DeadlineExceededError) as exc:
            status = "overload"
            metrics.inc("pii_backpressure_total", reason="restore-response")
            record_result(status)
            raise _map_limit_error(exc)
        except Exception as exc:
            record_result(status)
            raise _map_lifecycle_error(exc)
        finally:
            metrics.record_request(
                operation="restore_response", status=status,
                text_characters=len(req.response_text),
                elapsed_seconds=time.monotonic() - started_at,
            )

    return r


def _count_categories(resolved_spans) -> dict[str, int]:
    """Count masked spans per category from resolved spans (works for any mask mode)."""
    counts: dict[str, int] = {}
    for rs in resolved_spans:
        counts[rs.category] = counts.get(rs.category, 0) + 1
    return counts


def _spans_from_resolved(resolved_spans) -> list[SpanInfo]:
    """Convert resolved spans to UI-friendly SpanInfo (original coordinates)."""
    out: list[SpanInfo] = []
    for rs in resolved_spans:
        out.append(
            SpanInfo(
                start=rs.span.start,
                end=rs.span.end,
                category=rs.category,
                reason="MASK",
                rule_id=rs.rule_id or "",
            )
        )
    return out
