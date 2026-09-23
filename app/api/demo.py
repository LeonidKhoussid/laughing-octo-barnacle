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
    def mask(req: MaskRequest, x_api_key: str | None = Header(default=None)) -> MaskResponse:
        policy = policy_store.get_consumer(req.consumer)
        if policy is None or not policy.enabled:
            raise forbidden()
        if not verify_api_key(policy, x_api_key):
            raise unauthorized()

        try:
            limits.check_body(len(req.text.encode("utf-8")))
            limits.acquire()
        except (BackpressureError, BodyTooLargeError) as exc:
            metrics.inc("pii_backpressure_total", reason="mask")
            raise _map_limit_error(exc)
        try:
            request_id = logger.new_request_id()
            context_id = req.context_id or uuid.uuid4().hex
            started_at = time.monotonic()
            with metrics.timed("pii_request_duration_seconds", operation="mask"):
                outcome = lifecycle.mask(
                    policy.namespace, context_id, req.text, policy
                )
            limits.check_deadline(started_at)
            metrics.inc("pii_requests_total", operation="mask", status="success")
            logger.info(
                "mask completed",
                request_id=request_id,
                operation="mask",
                consumer=req.consumer,
                policy_version=policy_store.policy_version,
                stage="completed",
                detected_counts=_count_categories(outcome.resolved_spans),
                degraded=False,
                result="success",
            )
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
            raise
        except DeadlineExceededError as exc:
            metrics.inc("pii_requests_total", operation="mask", status="overload")
            raise _map_limit_error(exc)
        except Exception as exc:
            metrics.inc("pii_requests_total", operation="mask", status="error")
            raise _map_lifecycle_error(exc)
        finally:
            limits.release()

    @r.post("/unmask")
    def unmask(req: UnmaskRequest, x_api_key: str | None = Header(default=None)) -> UnmaskResponse:
        policy = policy_store.get_consumer(req.consumer)
        if policy is None or not policy.enabled:
            raise forbidden()
        if not verify_api_key(policy, x_api_key):
            raise unauthorized()
        # allow_unmask is re-checked at unmask time (R39).
        if not policy.allow_unmask:
            raise unmask_denied()

        try:
            limits.check_body(len(req.masked_text.encode("utf-8")))
            limits.acquire()
        except (BackpressureError, BodyTooLargeError) as exc:
            metrics.inc("pii_backpressure_total", reason="unmask")
            raise _map_limit_error(exc)
        try:
            request_id = logger.new_request_id()
            started_at = time.monotonic()
            with metrics.timed("pii_request_duration_seconds", operation="unmask"):
                original = lifecycle.unmask(
                    policy.namespace, req.context_id, req.masked_text, policy
                )
            limits.check_deadline(started_at)
            metrics.inc("pii_requests_total", operation="unmask", status="success")
            logger.info(
                "unmask completed",
                request_id=request_id,
                operation="unmask",
                consumer=req.consumer,
                policy_version=policy_store.policy_version,
                stage="completed",
                degraded=False,
                result="success",
            )
            return UnmaskResponse(
                original_text=original,
                policy_version=policy_store.policy_version,
            )
        except HTTPException:
            raise
        except DeadlineExceededError as exc:
            metrics.inc("pii_requests_total", operation="unmask", status="overload")
            raise _map_limit_error(exc)
        except Exception as exc:
            metrics.inc("pii_requests_total", operation="unmask", status="error")
            raise _map_lifecycle_error(exc)
        finally:
            limits.release()

    @r.post("/chat")
    def chat(req: ChatRequest, x_api_key: str | None = Header(default=None)) -> ChatResponse:
        policy = policy_store.get_consumer(req.consumer)
        if policy is None or not policy.enabled:
            raise forbidden()
        if not verify_api_key(policy, x_api_key):
            raise unauthorized()
        if not policy.allow_llm_egress:
            raise forbidden()

        try:
            limits.check_body(len(req.masked_text.encode("utf-8")))
            limits.acquire()
        except (BackpressureError, BodyTooLargeError) as exc:
            metrics.inc("pii_backpressure_total", reason="chat")
            raise _map_limit_error(exc)
        try:
            request_id = logger.new_request_id()
            started_at = time.monotonic()
            # Egress must depend on ACTUAL server-side processing, not caller
            # claims (C). Verify the masked_text corresponds to a committed
            # record for this context, and fetch the real mapping.
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
                policy,
                stub.provider_id,
                req.masked_text,
                stages_completed=["detect", "resolve", "mask"],
                remaining_sensitive_spans=0,
                mapping=mapping,
            )
            if not check.ok:
                metrics.inc("pii_egress_blocks_total", reason="policy")
                raise forbidden()
            response = stub.complete(req.masked_text)
            # Output protection: inspect the provider response for NEWLY
            # introduced sensitive content. Known tokens from the authorized
            # context are preserved (not re-detected); any new sensitive text
            # is masked before the response is returned (section 13, R47).
            protected = _protect_provider_output(engine, response, mapping)
            limits.check_deadline(started_at)
            metrics.inc("pii_requests_total", operation="chat", status="success")
            metrics.inc("pii_upstream_calls_total", provider="stub", result="success")
            logger.info(
                "chat completed",
                request_id=request_id,
                operation="chat",
                consumer=req.consumer,
                policy_version=policy_store.policy_version,
                stage="completed",
                degraded=False,
                result="success",
            )
            return ChatResponse(response=protected, provider=stub.provider_id, stub=True)
        except HTTPException:
            raise
        except DeadlineExceededError as exc:
            metrics.inc("pii_requests_total", operation="chat", status="overload")
            raise _map_limit_error(exc)
        except Exception as exc:
            metrics.inc("pii_requests_total", operation="chat", status="error")
            raise _map_lifecycle_error(exc)
        finally:
            limits.release()

    @r.post("/restore-response")
    def restore_response(
        req: RestoreResponseRequest, x_api_key: str | None = Header(default=None)
    ) -> RestoreResponseResponse:
        """Restore authorized tokens inside a changed provider response.

        The provider may prefix/reword its output, so the response is not expected
        to equal the stored masked_text. This performs EXACT token substitution
        (R25), never fuzzy/recursive/LLM-based demasking.
        """
        policy = policy_store.get_consumer(req.consumer)
        if policy is None or not policy.enabled:
            raise forbidden()
        if not verify_api_key(policy, x_api_key):
            raise unauthorized()
        if not policy.allow_unmask:
            raise unmask_denied()

        try:
            limits.check_body(len(req.response_text.encode("utf-8")))
            limits.acquire()
        except (BackpressureError, BodyTooLargeError) as exc:
            metrics.inc("pii_backpressure_total", reason="restore-response")
            raise _map_limit_error(exc)
        try:
            request_id = logger.new_request_id()
            started_at = time.monotonic()
            # Protect the response BEFORE restoring: mask any newly introduced
            # sensitive content so an alternate route cannot bypass output
            # protection (review issue 7). Known tokens are preserved.
            try:
                record = vault.get(policy.namespace, req.context_id)
            except Exception:  # noqa: BLE001
                record = None
            mapping = (record or {}).get("mapping") or {}
            protected = _protect_provider_output(engine, req.response_text, mapping)
            with metrics.timed("pii_request_duration_seconds", operation="restore_response"):
                restored = lifecycle.restore_tokens(
                    policy.namespace, req.context_id, protected, policy
                )
            limits.check_deadline(started_at)
            metrics.inc("pii_requests_total", operation="restore_response", status="success")
            logger.info(
                "restore-response completed",
                request_id=request_id,
                operation="restore_response",
                consumer=req.consumer,
                policy_version=policy_store.policy_version,
                stage="completed",
                degraded=False,
                result="success",
            )
            return RestoreResponseResponse(
                restored_text=restored,
                policy_version=policy_store.policy_version,
            )
        except HTTPException:
            raise
        except DeadlineExceededError as exc:
            metrics.inc("pii_requests_total", operation="restore_response", status="overload")
            raise _map_limit_error(exc)
        except Exception as exc:
            metrics.inc("pii_requests_total", operation="restore_response", status="error")
            raise _map_lifecycle_error(exc)
        finally:
            limits.release()

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
