"""RFC 9457 Problem Details.

`code` is the stable machine-readable contract the frontend switches on.
`title`/`detail` are human strings and may change without a version bump.
Never leak stack traces, SQL, or internal paths.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from docflow.core.logging import get_logger, request_id_var

logger = get_logger(__name__)

PROBLEM_BASE = "https://docflow.ai/errors"
CONTENT_TYPE = "application/problem+json"


class DocFlowError(Exception):
    """Base for every error we deliberately surface to a client."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "INTERNAL_ERROR"
    title: str = "Internal server error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        self.detail = detail or self.title
        self.errors = errors
        self.headers = headers or {}
        self.extra = extra
        super().__init__(self.detail)

    def to_problem(self, instance: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": f"{PROBLEM_BASE}/{self.code.lower().replace('_', '-')}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "instance": instance,
            "code": self.code,
            "request_id": request_id_var.get(),
        }
        if self.errors:
            body["errors"] = self.errors
        body.update(self.extra)
        return body


# --- 4xx ------------------------------------------------------------------


class ValidationFailed(DocFlowError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "VALIDATION_FAILED"
    title = "Request validation failed"


class Unauthenticated(DocFlowError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHENTICATED"
    title = "Authentication required"


class InvalidCredentials(DocFlowError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "INVALID_CREDENTIALS"
    title = "Invalid email or password"


class TokenExpired(DocFlowError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "TOKEN_EXPIRED"
    title = "Token has expired"


class TokenReuseDetected(DocFlowError):
    """A refresh token was replayed — the whole family is revoked."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "TOKEN_REUSE_DETECTED"
    title = "Session revoked for security reasons"


class Forbidden(DocFlowError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"
    title = "You do not have permission to perform this action"


class NotFound(DocFlowError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    title = "Resource not found"


class EmailAlreadyRegistered(DocFlowError):
    status_code = status.HTTP_409_CONFLICT
    code = "EMAIL_ALREADY_REGISTERED"
    title = "That email is already registered"


class DuplicateDocument(DocFlowError):
    status_code = status.HTTP_409_CONFLICT
    code = "DUPLICATE_DOCUMENT"
    title = "This document has already been uploaded"


class IdempotencyKeyReuse(DocFlowError):
    status_code = status.HTTP_409_CONFLICT
    code = "IDEMPOTENCY_KEY_REUSE"
    title = "Idempotency key reused with a different request body"


class QuotaExceeded(DocFlowError):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    code = "QUOTA_EXCEEDED"
    title = "Plan quota exceeded"


class BudgetExceeded(DocFlowError):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    code = "BUDGET_EXCEEDED"
    title = "AI spending budget exceeded"


class DocumentTooLarge(DocFlowError):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    code = "DOCUMENT_TOO_LARGE"
    title = "Document exceeds the size limit"


class InvalidFileType(DocFlowError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "INVALID_FILE_TYPE"
    title = "Unsupported file type"


class CorruptDocument(DocFlowError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "CORRUPT_DOCUMENT"
    title = "Document could not be read"


class EncryptedDocument(DocFlowError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "ENCRYPTED_DOCUMENT"
    title = "Document is password-protected"


class TooManyPages(DocFlowError):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    code = "TOO_MANY_PAGES"
    title = "Document has too many pages"


class RateLimited(DocFlowError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMITED"
    title = "Too many requests"


class ProviderUnavailable(DocFlowError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "PROVIDER_UNAVAILABLE"
    title = "An upstream AI provider is unavailable"


# --- handlers -------------------------------------------------------------


def _respond(exc: DocFlowError, request: Request) -> JSONResponse:
    headers = {"X-Request-ID": request_id_var.get() or "", **exc.headers}
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_problem(str(request.url.path)),
        media_type=CONTENT_TYPE,
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DocFlowError)
    async def _docflow(request: Request, exc: DocFlowError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("error.server", code=exc.code, detail=exc.detail)
        else:
            logger.info("error.client", code=exc.code, status=exc.status_code)
        return _respond(exc, request)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]),
                "message": e["msg"],
                "type": e["type"],
            }
            for e in exc.errors()
        ]
        return _respond(ValidationFailed("One or more fields are invalid.", errors=errors), request)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        mapped = DocFlowError(str(exc.detail))
        mapped.status_code = exc.status_code
        mapped.code = {
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            401: "UNAUTHENTICATED",
            403: "FORBIDDEN",
        }.get(exc.status_code, "HTTP_ERROR")
        mapped.title = str(exc.detail)
        return _respond(mapped, request)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("error.unhandled", error_type=type(exc).__name__)
        # Deliberately opaque: never leak internals to a client.
        return _respond(
            DocFlowError("An unexpected error occurred. The incident has been logged."),
            request,
        )
