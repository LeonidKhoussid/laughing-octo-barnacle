"""Safe error responses that do NOT reflect input values."""
from __future__ import annotations

from fastapi import HTTPException, status


def safe_error(status_code: int, code: str, message: str, headers: dict | None = None) -> HTTPException:
    """Build an HTTPException with a safe, generic message (no input reflection)."""
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers=headers,
    )


def unauthorized() -> HTTPException:
    return safe_error(status.HTTP_401_UNAUTHORIZED, "unauthorized", "authentication required")


def forbidden() -> HTTPException:
    return safe_error(status.HTTP_403_FORBIDDEN, "forbidden", "access denied")


def not_found() -> HTTPException:
    return safe_error(status.HTTP_404_NOT_FOUND, "not_found", "resource not found")


def context_missing() -> HTTPException:
    return safe_error(
        status.HTTP_404_NOT_FOUND, "context_missing", "context not found or expired"
    )


def unmask_denied() -> HTTPException:
    return safe_error(status.HTTP_403_FORBIDDEN, "unmask_denied", "unmasking not allowed")


def conflict() -> HTTPException:
    return safe_error(status.HTTP_409_CONFLICT, "conflict", "text does not match the record for this id")


def retryable() -> HTTPException:
    return safe_error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "retryable",
        "temporarily unavailable; retry later",
        headers={"Retry-After": "1"},
    )


def overloaded() -> HTTPException:
    """Backpressure for the demo API: too many requests in flight. Retryable
    with a valid integer-seconds Retry-After (R57). Never reflects input."""
    return safe_error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "overloaded",
        "server is overloaded; retry later",
        headers={"Retry-After": "1"},
    )


def overloaded_429() -> HTTPException:
    """Managed overload for the official /process endpoint. Returns 429 with a
    valid integer-seconds Retry-After (Appendix B: the checker applies wait +
    retries and honors Retry-After). Never reflects input values."""
    return safe_error(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "overloaded",
        "server is overloaded; retry later",
        headers={"Retry-After": "1"},
    )


def too_large() -> HTTPException:
    """Request body exceeds the configured limit. Safe, no input reflection."""
    return safe_error(
        status.HTTP_413_CONTENT_TOO_LARGE,
        "too_large",
        "request body exceeds the size limit",
    )


def integrity_error() -> HTTPException:
    return safe_error(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "integrity_error",
        "stored record integrity check failed",
    )


def processing_error() -> HTTPException:
    return safe_error(
        status.HTTP_422_UNPROCESSABLE_ENTITY, "processing_error", "processing failed"
    )
