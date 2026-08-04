"""Redis client, JSON cache helper, and token revocation list."""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: Redis | None = None


def get_redis() -> Redis:
    """Return the lazily-created shared Redis client."""
    global _client
    if _client is None:
        _client = aioredis.from_url(
            str(settings.REDIS_URL),
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
        )
    return _client


async def close_redis() -> None:
    """Close the Redis connection pool during shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class CacheService:
    """JSON cache with fail-open semantics.

    A cache outage degrades performance, never correctness: every failure is
    logged and treated as a miss so requests still succeed against the
    source of truth.
    """

    def __init__(self, client: Redis | None = None) -> None:
        self._redis = client or get_redis()

    async def get(self, key: str) -> Any | None:
        try:
            raw = await self._redis.get(key)
        except RedisError as exc:
            logger.warning("Cache read failed for %s: %s", key, exc)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Discarding corrupt cache entry: %s", key)
            await self.delete(key)
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        try:
            await self._redis.set(
                key,
                json.dumps(value, default=str),
                ex=ttl or settings.CACHE_DEFAULT_TTL_SECONDS,
            )
            return True
        except (RedisError, TypeError) as exc:
            logger.warning("Cache write failed for %s: %s", key, exc)
            return False

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        try:
            return int(await self._redis.delete(*keys))
        except RedisError as exc:
            logger.warning("Cache delete failed: %s", exc)
            return 0

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every key under a prefix using non-blocking SCAN."""
        deleted = 0
        try:
            async for key in self._redis.scan_iter(match=f"{prefix}*", count=500):
                deleted += int(await self._redis.delete(key))
        except RedisError as exc:
            logger.warning("Cache prefix delete failed for %s: %s", prefix, exc)
        return deleted

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except RedisError:
            return False


class TokenDenyList:
    """Tracks revoked JWT ids until their natural expiry."""

    _PREFIX = "revoked_jti:"

    def __init__(self, client: Redis | None = None) -> None:
        self._redis = client or get_redis()

    async def revoke(self, jti: str, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        try:
            await self._redis.set(f"{self._PREFIX}{jti}", "1", ex=ttl_seconds)
        except RedisError as exc:
            logger.error("Failed to revoke token %s: %s", jti, exc)
            raise

    async def is_revoked(self, jti: str) -> bool:
        """Fail closed: if Redis is unreachable, treat the token as revoked."""
        try:
            return bool(await self._redis.exists(f"{self._PREFIX}{jti}"))
        except RedisError as exc:
            logger.error("Deny-list check failed for %s: %s", jti, exc)
            return True
