"""Structured error envelope and shared exception handlers.

Every error response in the service has the shape::

    {"error": {"code": "STABLE_CODE", "message": "human readable", "details": {...}}}

Handlers are registered once in ``app.main`` via :func:`register_exception_handlers`.
Feature code raises :class:`AppError` subclasses instead of ``HTTPException`` so
the envelope stays consistent. Canonical codes are catalogued in DECISIONS.md §6.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("back_cart.errors")

# HTTP status -> stable error code for framework-raised HTTPExceptions
# (unmatched routes, wrong method, etc.) that don't come from an AppError.
_STATUS_CODES: dict[int, str] = {
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "VALIDATION_ERROR",
}


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    """Response model for every error path — reference it in ``responses={}``."""

    error: ErrorBody


class AppError(Exception):
    """Base class for expected, business-level failures.

    Unexpected system failures (bugs, DB down) are NOT AppErrors — they fall
    through to the generic 500 handler with a different code and log severity.
    """

    code: str = "APP_ERROR"
    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = status.HTTP_404_NOT_FOUND


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class ConflictError(AppError):
    """A request that is well-formed but conflicts with current state (409)."""

    code = "CONFLICT"
    status_code = status.HTTP_409_CONFLICT


class CartAlreadyCheckedOutError(ConflictError):
    code = "CART_ALREADY_CHECKED_OUT"


class InsufficientInventoryError(ConflictError):
    code = "INSUFFICIENT_INVENTORY"


class EmptyCartError(ValidationError):
    code = "EMPTY_CART"


class IdempotencyKeyReuseError(ConflictError):
    """The same Idempotency-Key was presented for a different cart."""

    code = "IDEMPOTENCY_KEY_REUSED"


class InvalidCouponError(ValidationError):
    code = "INVALID_COUPON"


class CouponAlreadyRedeemedError(ConflictError):
    code = "COUPON_ALREADY_REDEEMED"


class MilestoneNotReachedError(ValidationError):
    code = "MILESTONE_NOT_REACHED"


class MilestoneAlreadyRewardedError(ConflictError):
    code = "MILESTONE_ALREADY_REWARDED"


def _envelope(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.message, exc.details),
    )


async def _request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_envelope(
            "VALIDATION_ERROR",
            "Request validation failed.",
            {"errors": jsonable_encoder(exc.errors())},
        ),
    )


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Wrap framework HTTPExceptions (unmatched route, wrong method, …) in the
    same envelope so no error response escapes it."""
    code = _STATUS_CODES.get(exc.status_code, "HTTP_ERROR")
    message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ").title()
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(code, message, {}),
        headers=getattr(exc, "headers", None),
    )


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Last resort: an unexpected failure (a bug, the DB down). Logged at ERROR;
    the client gets the envelope with no internal detail leaked."""
    logger.exception(
        "unhandled error on %s %s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope(
            "INTERNAL_ERROR", "An unexpected error occurred.", {}
        ),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _request_validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
