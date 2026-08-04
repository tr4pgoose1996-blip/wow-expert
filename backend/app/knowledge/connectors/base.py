"""Connector base class and registry.

A connector converts one external source into :class:`KnowledgeDocument`
objects. Everything downstream — chunking, embedding, storage, retrieval — is
source-agnostic, so adding Petopia or a new wiki means writing one class and
registering it. Nothing else changes.

The base class enforces the two things every connector must honour: the
source's usage tier, and a polite request rate.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import ClassVar

import httpx

from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.knowledge.documents import KnowledgeDocument
from app.knowledge.policy import (
    PolicyViolationError,
    SourceKey,
    SourcePolicy,
    UsageTier,
    get_policy,
)

logger = get_logger(__name__)

__all__ = [
    "BaseConnector",
    "available_connectors",
    "get_connector",
    "register_connector",
]


class _RateLimiter:
    """Enforces a minimum interval between requests to one host.

    Deliberately conservative and per-connector. Being a well-behaved client
    of community fan sites is both an ethical obligation and the practical
    condition of continued access.
    """

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            elapsed = time.monotonic() - self._last
            wait = self._min_interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class BaseConnector(ABC):
    """Base class for all knowledge sources."""

    #: Which registered source this connector speaks for.
    source: ClassVar[SourceKey]

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.policy: SourcePolicy = get_policy(self.source)
        if self.policy.tier is UsageTier.DISABLED:
            raise PolicyViolationError(
                f"Connector for {self.policy.display_name} cannot be "
                f"instantiated: {self.policy.rationale}"
            )
        self._limiter = _RateLimiter(self.policy.min_request_interval)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": self.policy.user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
        )

    @property
    def tier(self) -> UsageTier:
        return self.policy.tier

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        max_retries: int = 3,
    ) -> dict:
        """Rate-limited GET returning parsed JSON, with backoff on 429/5xx."""
        for attempt in range(1, max_retries + 1):
            await self._limiter.acquire()
            try:
                response = await self._client.get(
                    url, params=params, headers=headers
                )
                if response.status_code == 429:
                    retry_after = float(
                        response.headers.get("Retry-After", 2**attempt)
                    )
                    logger.warning(
                        "%s rate-limited us; backing off %.1fs",
                        self.policy.display_name, retry_after,
                    )
                    await asyncio.sleep(min(retry_after, 60.0))
                    continue
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "upstream error",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                if attempt == max_retries:
                    raise ExternalServiceError(
                        f"{self.policy.display_name} is unavailable: {exc}"
                    ) from exc
                await asyncio.sleep(min(2.0**attempt, 30.0))
        raise ExternalServiceError(
            f"{self.policy.display_name} exhausted retries."
        )

    @abstractmethod
    def fetch(self) -> AsyncIterator[KnowledgeDocument]:
        """Yield documents from this source.

        Implemented as an async generator so ingestion streams rather than
        materialising an entire source in memory — some of these are very
        large.
        """
        raise NotImplementedError

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()


_REGISTRY: dict[SourceKey, type[BaseConnector]] = {}


def register_connector(cls: type[BaseConnector]) -> type[BaseConnector]:
    """Class decorator registering a connector against its source."""
    if not hasattr(cls, "source"):
        raise TypeError(f"{cls.__name__} must declare a 'source' class attribute")
    _REGISTRY[cls.source] = cls
    return cls


def get_connector(source: SourceKey | str) -> type[BaseConnector]:
    """Look up a registered connector class."""
    key = SourceKey(source)
    if key not in _REGISTRY:
        raise KeyError(
            f"No connector registered for {key.value}. "
            f"Available: {sorted(k.value for k in _REGISTRY)}"
        )
    return _REGISTRY[key]


def available_connectors() -> dict[SourceKey, type[BaseConnector]]:
    """All registered connectors, excluding policy-disabled sources."""
    return {
        key: cls
        for key, cls in _REGISTRY.items()
        if get_policy(key).tier is not UsageTier.DISABLED
    }
