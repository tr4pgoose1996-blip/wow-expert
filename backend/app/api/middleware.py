"""Request correlation, access logging, security headers, and rate limiting."""

from __future__ import annotations

import time
import uuid

from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.logging import get_logger, request_id_ctx
from app.core.redis import get_redis

logger = get_logger(__name__)

SAFE_PATHS = frozenset({"/health", "/health/ready", "/metrics"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, log the access line, and time the request."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_ctx.set(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "Request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise
        finally:
            request_id_ctx.reset(token)

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-ms"] = f"{duration_ms:.2f}"

        if request.url.path not in SAFE_PATHS:
            logger.info(
                "%s %s -> %s",
                request.method,
                request.url.path,
                response.status_code,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply conservative security headers to every response."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-client rate limiting backed by Redis.

    Fails open: if Redis is unavailable the request proceeds, because
    dropping all traffic is worse than briefly losing throttling.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not settings.RATE_LIMIT_ENABLED or request.url.path in SAFE_PATHS:
            return await call_next(request)

        identity = self._identify(request)
        window = settings.RATE_LIMIT_WINDOW_SECONDS
        bucket = int(time.time()) // window
        key = f"ratelimit:{identity}:{bucket}"

        try:
            redis = get_redis()
            pipeline = redis.pipeline()
            pipeline.incr(key)
            pipeline.expire(key, window + 1)
            count = int((await pipeline.execute())[0])
        except RedisError as exc:
            logger.warning("Rate limiter unavailable, allowing request: %s", exc)
            return await call_next(request)

        limit = settings.RATE_LIMIT_REQUESTS
        if count > limit:
            retry_after = window - (int(time.time()) % window)
            logger.warning("Rate limit exceeded", extra={"identity": identity})
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests. Please slow down.",
                        "details": {"retry_after_seconds": retry_after},
                    },
                    "request_id": request_id_ctx.get(),
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - count))
        return response

    @staticmethod
    def _identify(request: Request) -> str:
        """Prefer the authenticated subject; fall back to client IP."""
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            # Hash-free short prefix is enough to bucket a session without
            # logging or storing the token itself.
            return f"token:{hash(auth[7:]) & 0xFFFFFFFF:08x}"
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"
        return f"ip:{request.client.host if request.client else 'unknown'}"
