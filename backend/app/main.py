"""FastAPI application factory and entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.api.v1.endpoints import health as health_endpoints
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.redis import CacheService, close_redis
from app.db.session import dispose_engine
from app.modules import registry
from app.services.ai_provider import close_ai_provider
from app.services.sync_scheduler import scheduler

logger = get_logger(__name__)

DESCRIPTION = """
**wow! expert.** is an AI-powered World of Warcraft Game Master, guide,
coach, and companion.

### Getting started
1. `POST /api/v1/auth/register` to create an account.
2. `POST /api/v1/auth/login` to obtain an access/refresh token pair.
3. Send `Authorization: Bearer <access_token>` on protected routes.
4. `POST /api/v1/characters` to add a character.
5. `POST /api/v1/modules/guidance/ask` for character-aware coaching.

### Errors
Every failure returns the same envelope:

```json
{
  "error": {"code": "not_found", "message": "...", "details": {}},
  "request_id": "..."
}
```

Branch on the stable `error.code`, not the human-readable message.
"""

TAGS_METADATA = [
    {"name": "auth", "description": "Registration, login, and token lifecycle."},
    {"name": "characters", "description": "Character roster management."},
    {
        "name": "blizzard",
        "description": (
            "Battle.net OAuth linking, character import and synchronization, "
            "collections, and realm/guild lookup."
        ),
    },
    {"name": "health", "description": "Liveness and readiness probes."},
    *[
        {"name": module.name, "description": module.description}
        for module in registry.active
    ],
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start and stop shared resources exactly once per process."""
    configure_logging(settings.LOG_LEVEL, settings.LOG_JSON)
    logger.info(
        "Starting %s in %s mode", settings.PROJECT_NAME, settings.ENVIRONMENT
    )

    if not await CacheService().ping():
        # Non-fatal: the cache layer degrades gracefully, but operators
        # should see this immediately at boot.
        logger.warning("Redis is unreachable at startup; caching is degraded.")

    await registry.startup_all()
    logger.info("Active modules: %s", [m.name for m in registry.active] or "none")

    await scheduler.start()

    try:
        yield
    finally:
        logger.info("Shutting down %s", settings.PROJECT_NAME)
        await scheduler.stop()
        await registry.shutdown_all()
        await close_ai_provider()
        await close_redis()
        await dispose_engine()


def create_app() -> FastAPI:
    """Build and configure the application instance."""
    app = FastAPI(
        title=settings.PROJECT_NAME,
        description=DESCRIPTION,
        version="0.1.0",
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # Middleware runs bottom-up: request context wraps everything so that
    # even rate-limit rejections carry a request id.
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Response-Time-ms"],
    )

    register_exception_handlers(app)

    app.include_router(health_endpoints.router, prefix="/health", tags=["health"])
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": settings.PROJECT_NAME,
            "version": app.version,
            "docs": "/docs" if not settings.is_production else "disabled",
        }

    return app


app = create_app()
