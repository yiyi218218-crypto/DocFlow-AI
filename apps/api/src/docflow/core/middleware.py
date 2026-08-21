"""Request middleware: correlation, security headers, rate limiting.

Order matters and is asserted by the order they are added in main.py:
request-id first (so everything downstream can log it), then security headers,
then rate limiting (so a throttled request is still correlated).
"""

from __future__ import annotations

import time
import uuid
from typing import ClassVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from docflow.core.config import settings
from docflow.core.logging import get_logger, request_id_var, user_id_var, workspace_id_var

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a request id for the whole request and return it to the client.

    The same id is put into every background job payload, so one grep spans the
    API and the worker. Without that link a user's "my document failed" report
    dead-ends at the 202.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and len(incoming) <= 64 else str(uuid.uuid4())

        token = request_id_var.set(request_id)
        ws_token = workspace_id_var.set(None)
        user_token = user_id_var.set(None)
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            request_id_var.reset(token)
            workspace_id_var.reset(ws_token)
            user_id_var.reset(user_token)

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers["Server-Timing"] = f"app;dur={duration_ms}"

        if not request.url.path.startswith("/health"):
            logger.info(
                "http.request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Headers asserted by tests/security/test_headers.py.

    The API serves JSON, so its CSP is maximally restrictive — the page CSP that
    actually matters is set by Next.js with a per-request nonce.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        # Never cache an authenticated API response in a shared cache.
        response.headers.setdefault("Cache-Control", "private, no-store")

        if not settings.is_local:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis token bucket, per user when authenticated and per IP otherwise.

    Fails OPEN: if Redis is unavailable the request proceeds. A rate limiter that
    takes the whole API down when its own dependency blips is a worse outage than
    the abuse it prevents.
    """

    # (requests, per_seconds) by endpoint class.
    LIMITS: ClassVar[dict[str, tuple[int, int]]] = {
        "auth": (5, 60),
        "upload": (20, 3600),
        "ai": (30, 60),
        "read": (300, 60),
    }

    def _bucket(self, request: Request) -> str:
        path = request.url.path
        if path.startswith("/api/v1/auth"):
            return "auth"
        if path.startswith("/api/v1/documents") and request.method == "POST":
            return "upload"
        if any(path.startswith(p) for p in ("/api/v1/chat", "/api/v1/search")):
            return "ai"
        return "read"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith("/health") or request.method == "OPTIONS":
            return await call_next(request)

        bucket = self._bucket(request)
        limit, window = self.LIMITS[bucket]
        # Production limits throttle a developer or an E2E run into uselessness
        # (five logins a minute is one test file). The limiter still runs and
        # still emits RateLimit-* headers, so the behaviour stays covered.
        if settings.is_local:
            limit *= 20
        identity = getattr(request.state, "user_id", None) or _client_ip(request)
        key = f"rl:{bucket}:{identity}"

        try:
            from docflow.worker.queue import get_queue

            redis = await get_queue()
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, window)
            ttl = await redis.ttl(key)
        except Exception:
            return await call_next(request)

        if count > limit:
            logger.warning("ratelimit.exceeded", bucket=bucket, path=request.url.path)
            return JSONResponse(
                status_code=429,
                media_type="application/problem+json",
                content={
                    "type": "https://docflow.ai/errors/rate-limited",
                    "title": "Too many requests",
                    "status": 429,
                    "detail": f"Rate limit of {limit} requests per {window}s exceeded.",
                    "code": "RATE_LIMITED",
                    "request_id": request_id_var.get(),
                },
                headers={
                    "Retry-After": str(max(ttl, 1)),
                    "RateLimit-Limit": str(limit),
                    "RateLimit-Remaining": "0",
                    "RateLimit-Reset": str(max(ttl, 1)),
                },
            )

        response = await call_next(request)
        response.headers["RateLimit-Limit"] = str(limit)
        response.headers["RateLimit-Remaining"] = str(max(0, limit - count))
        response.headers["RateLimit-Reset"] = str(max(ttl, 0))
        return response


def _client_ip(request: Request) -> str:
    """Trust X-Forwarded-For only behind a proxy that sets it.

    Taking the leftmost entry unconditionally lets a client spoof its own
    identity and evade per-IP limits, so this is gated on deployment context.
    """
    if not settings.is_local:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
