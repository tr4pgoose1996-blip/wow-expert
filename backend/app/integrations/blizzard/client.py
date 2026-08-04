"""Resilient HTTP client for the Blizzard Game Data and Profile APIs.

Responsibilities layered here so callers get them for free:

* automatic application-token acquisition and refresh (401 -> re-auth once)
* bounded concurrency respecting Blizzard's per-client rate limits
* exponential backoff with jitter on 429 and 5xx
* Redis-backed response caching keyed by path, namespace, and query
* ``404`` surfaced as ``None`` rather than an exception, because a missing
  character or guild is an ordinary outcome, not a failure
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import ExternalServiceError, NotFoundError
from app.core.logging import get_logger
from app.core.redis import CacheService
from app.integrations.blizzard.constants import Namespace, api_host
from app.integrations.blizzard.oauth import BlizzardOAuthClient

logger = get_logger(__name__)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class BlizzardAPIClient:
    """Async client for Blizzard's WoW APIs."""

    def __init__(
        self,
        oauth: BlizzardOAuthClient | None = None,
        cache: CacheService | None = None,
        http_client: httpx.AsyncClient | None = None,
        region: str | None = None,
    ) -> None:
        self.region = region or settings.BLIZZARD_REGION
        self._oauth = oauth or BlizzardOAuthClient()
        self._cache = cache or CacheService()
        self._client = http_client or httpx.AsyncClient(
            base_url=api_host(self.region),
            timeout=httpx.Timeout(settings.BLIZZARD_TIMEOUT_SECONDS),
            limits=httpx.Limits(
                max_connections=settings.BLIZZARD_MAX_CONCURRENCY * 2,
                max_keepalive_connections=settings.BLIZZARD_MAX_CONCURRENCY,
            ),
            follow_redirects=True,
        )
        self._semaphore = asyncio.Semaphore(settings.BLIZZARD_MAX_CONCURRENCY)

    # -- Public API --------------------------------------------------------

    async def get(
        self,
        path: str,
        *,
        namespace: Namespace = Namespace.PROFILE,
        params: dict[str, Any] | None = None,
        access_token: str | None = None,
        cache_ttl: int | None = None,
        locale: str | None = None,
    ) -> dict[str, Any] | None:
        """Perform a cached, retrying GET against the Blizzard API.

        Args:
            path: API path beginning with ``/``.
            namespace: Namespace category; the region suffix is applied here.
            params: Extra query parameters.
            access_token: A user token for account-scoped endpoints. When
                omitted the shared application token is used.
            cache_ttl: Cache lifetime in seconds. ``0`` disables caching,
                which is correct for user-scoped responses.
            locale: Override the configured locale.

        Returns:
            The decoded JSON body, or ``None`` when Blizzard returns 404.
        """
        query: dict[str, Any] = {
            "namespace": namespace.for_region(self.region),
            "locale": locale or settings.BLIZZARD_LOCALE,
            **(params or {}),
        }

        # Never cache user-scoped responses under a shared key: doing so
        # would leak one player's collection to another.
        is_user_scoped = access_token is not None
        effective_ttl = 0 if is_user_scoped else cache_ttl

        cache_key = self._cache_key(path, query) if effective_ttl else None
        if cache_key:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return cached if cached != {"__404__": True} else None

        payload = await self._request_with_retries(path, query, access_token)

        if cache_key:
            # Cache negative lookups briefly too, so a bad name typed
            # repeatedly does not hammer the upstream API.
            await self._cache.set(
                cache_key,
                payload if payload is not None else {"__404__": True},
                ttl=effective_ttl if payload is not None else 300,
            )

        return payload

    async def get_or_404(
        self, path: str, *, resource: str = "Resource", **kwargs: Any
    ) -> dict[str, Any]:
        """Like :meth:`get`, but raises when the resource does not exist."""
        payload = await self.get(path, **kwargs)
        if payload is None:
            raise NotFoundError(f"{resource} was not found on Blizzard's servers.")
        return payload

    async def get_many(
        self, requests: list[dict[str, Any]]
    ) -> list[dict[str, Any] | None]:
        """Fetch several endpoints concurrently.

        A failure in one request yields ``None`` for that slot instead of
        aborting the batch — a character sync should still import equipment
        when, say, the professions endpoint is briefly unavailable.
        """

        async def _safe(spec: dict[str, Any]) -> dict[str, Any] | None:
            path = spec.pop("path")
            try:
                return await self.get(path, **spec)
            except ExternalServiceError as exc:
                logger.warning("Batch request to %s failed: %s", path, exc.message)
                return None

        return list(
            await asyncio.gather(*(_safe(dict(spec)) for spec in requests))
        )

    # -- Internals ---------------------------------------------------------

    def _cache_key(self, path: str, query: dict[str, Any]) -> str:
        canonical = json.dumps(query, sort_keys=True, default=str)
        digest = hashlib.sha256(f"{path}|{canonical}".encode()).hexdigest()[:32]
        return f"blizzard:{self.region}:{digest}"

    async def _request_with_retries(
        self,
        path: str,
        query: dict[str, Any],
        access_token: str | None,
    ) -> dict[str, Any] | None:
        last_error: Exception | None = None
        reauthorized = False

        for attempt in range(settings.BLIZZARD_MAX_RETRIES):
            token = access_token or await self._oauth.get_app_token()

            try:
                async with self._semaphore:
                    response = await self._client.get(
                        path,
                        params=query,
                        headers={"Authorization": f"Bearer {token}"},
                    )
            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning(
                    "Blizzard request to %s timed out (attempt %s/%s)",
                    path, attempt + 1, settings.BLIZZARD_MAX_RETRIES,
                )
                await self._backoff(attempt)
                continue
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("Blizzard transport error for %s: %s", path, exc)
                await self._backoff(attempt)
                continue

            if response.status_code == 404:
                return None

            if response.status_code == 401 and access_token is None:
                # The application token was rejected. Refresh once, then give
                # up rather than looping against a credential problem.
                if reauthorized:
                    raise ExternalServiceError(
                        "Blizzard rejected the application credentials."
                    )
                reauthorized = True
                logger.info("Application token rejected; re-authenticating.")
                self._oauth.invalidate_app_token()
                continue

            if response.status_code == 401:
                raise ExternalServiceError(
                    "The Battle.net authorization has expired. "
                    "Please reconnect your account."
                )

            if response.status_code == 403:
                raise ExternalServiceError(
                    "Battle.net denied access to this resource. The required "
                    "scope may not have been granted."
                )

            if response.status_code in _RETRYABLE_STATUS:
                last_error = httpx.HTTPStatusError(
                    f"status {response.status_code}",
                    request=response.request,
                    response=response,
                )
                retry_after = self._retry_after(response)
                logger.warning(
                    "Blizzard returned %s for %s (attempt %s/%s)",
                    response.status_code, path, attempt + 1,
                    settings.BLIZZARD_MAX_RETRIES,
                )
                await self._backoff(attempt, retry_after)
                continue

            if response.is_success:
                try:
                    return response.json()
                except json.JSONDecodeError as exc:
                    raise ExternalServiceError(
                        "Blizzard returned a malformed response."
                    ) from exc

            logger.error(
                "Unexpected Blizzard status %s for %s: %s",
                response.status_code, path, response.text[:300],
            )
            raise ExternalServiceError(
                f"Blizzard returned an unexpected status ({response.status_code})."
            )

        raise ExternalServiceError(
            "Blizzard's API is unavailable after several attempts."
        ) from last_error

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        header = response.headers.get("Retry-After")
        if not header:
            return None
        try:
            return float(header)
        except ValueError:
            return None

    @staticmethod
    async def _backoff(attempt: int, retry_after: float | None = None) -> None:
        """Sleep with exponential backoff plus jitter.

        Jitter matters here: without it, a fleet of workers rate-limited at
        the same instant would retry in lockstep and be throttled again.
        """
        if retry_after is not None:
            delay = min(retry_after, 30.0)
        else:
            delay = min(2.0**attempt, 16.0)
        await asyncio.sleep(delay + random.uniform(0, 0.5))

    async def close(self) -> None:
        await self._client.aclose()
        await self._oauth.close()
