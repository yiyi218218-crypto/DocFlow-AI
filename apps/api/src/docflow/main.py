"""FastAPI application.

Middleware order is load-bearing and is asserted by tests/security/test_headers.py.
Starlette applies middleware in reverse registration order, so the last one added
runs outermost — request context must be registered last so every other layer,
including error responses, carries the correlation id.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from docflow.api.v1.router import api_router
from docflow.core.config import settings
from docflow.core.database import check_database
from docflow.core.errors import register_exception_handlers
from docflow.core.logging import configure_logging, get_logger
from docflow.core.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from docflow.schemas.common import HealthResponse

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(
        level=settings.log_level,
        json_output=not settings.is_local,
        service="api",
    )
    logger.info(
        "api.starting",
        environment=settings.environment,
        version=settings.version,
        demo_mode=settings.effective_demo_mode,
    )

    if settings.is_local:
        # Convenience only: create the bucket so a fresh clone works without a
        # manual MinIO step. Never attempted in a deployed environment.
        from docflow.core import storage

        try:
            await storage.ensure_bucket()
        except Exception:
            logger.warning("api.bucket_check_failed")

    if not settings.is_local:
        # A pooler in session mode makes SET LOCAL leak across requests, which
        # silently downgrades tenant isolation to none. Fail loudly at boot.
        from docflow.core.database import assert_transaction_pooling

        await assert_transaction_pooling()

    yield

    from docflow.worker.queue import close_queue

    await close_queue()
    logger.info("api.stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="DocFlow AI",
        description=(
            "Turn documents into decisions, deadlines, tasks and actions — with "
            "every fact traceable to its source span."
        ),
        version=settings.version,
        lifespan=lifespan,
        docs_url="/docs" if settings.is_local else None,
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    # Registered inner-to-outer; request context ends up outermost.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Workspace-Id",
            "X-Request-ID",
            "Idempotency-Key",
            "If-Match",
        ],
        expose_headers=[
            "X-Request-ID",
            "RateLimit-Limit",
            "RateLimit-Remaining",
            "RateLimit-Reset",
            "Retry-After",
            "ETag",
        ],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        """Liveness. Deliberately checks nothing external — a dependency blip
        must not make the orchestrator kill a healthy process."""
        return HealthResponse(
            status="ok",
            version=settings.version,
            environment=settings.environment,
            demo_mode=settings.effective_demo_mode,
        )

    @app.get("/health/ready", response_model=HealthResponse, tags=["system"])
    async def ready() -> HealthResponse:
        """Readiness. Checks every dependency a request actually needs."""
        from docflow.core.storage import check_storage
        from docflow.worker.queue import check_redis

        checks = {
            "database": await check_database(),
            "redis": await check_redis(),
            "storage": await check_storage(),
        }
        return HealthResponse(
            status="ok" if all(checks.values()) else "degraded",
            version=settings.version,
            environment=settings.environment,
            checks=checks,
            demo_mode=settings.effective_demo_mode,
        )

    return app


app = create_app()
