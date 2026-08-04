"""Liveness and readiness probes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.dependencies import SessionDep
from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import CacheService
from app.modules.registry import registry

logger = get_logger(__name__)
router = APIRouter()


@router.get(
    "",
    summary="Liveness probe",
    description="Returns 200 whenever the process is running.",
)
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
    }


@router.get(
    "/ready",
    summary="Readiness probe",
    description=(
        "Verifies the database, cache, and every registered module. "
        "Returns 503 when any dependency is unhealthy."
    ),
)
async def readiness(session: SessionDep, response: Response) -> dict[str, Any]:
    checks: dict[str, Any] = {}

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as exc:
        logger.error("Database readiness check failed: %s", exc)
        checks["database"] = {"status": "error", "detail": str(exc)}

    checks["cache"] = (
        {"status": "ok"} if await CacheService().ping()
        else {"status": "error", "detail": "Redis is unreachable."}
    )

    checks["modules"] = await registry.health_all()

    def _degraded(node: Any) -> bool:
        if isinstance(node, dict):
            if node.get("status") not in (None, "ok"):
                return True
            return any(_degraded(v) for v in node.values())
        return False

    healthy = not _degraded(checks)
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ok" if healthy else "degraded", "checks": checks}
