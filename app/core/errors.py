"""Structured error envelope and shared exception handlers.

Every error response in the service has the shape::

    {"error": {"code": "STABLE_CODE", "message": "human readable", "details": {...}}}

Handlers are registered once in ``app.main`` via :func:`register_exception_handlers`.
Feature code raises :class:`AppError` subclasses instead of ``HTTPException`` so
the envelope stays consistent. Canonical codes are catalogued in DECISIONS.md §6.
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


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


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _request_validation_handler)
