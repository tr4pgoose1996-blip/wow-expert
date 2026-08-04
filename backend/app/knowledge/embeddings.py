"""Embedding provider abstraction.

Mirrors the ``AIProvider`` pattern: the pipeline depends only on the
:class:`EmbeddingProvider` interface, so switching vendors — or running the
whole engine offline in CI — is a configuration change.

The deterministic hashing provider is not a placeholder. It is a real,
self-consistent embedding function that makes the entire retrieval stack
testable without network access or API spend: identical text always produces
an identical vector, and lexically similar text produces measurably closer
vectors. It is not semantically meaningful and must not be used in production.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from abc import ABC, abstractmethod
from functools import lru_cache
from itertools import pairwise

import httpx

from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "EmbeddingProvider",
    "HashingEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "close_embedding_provider",
    "get_embedding_provider",
]

_TOKEN = re.compile(r"[a-z0-9']+")


class EmbeddingProvider(ABC):
    """Turns text into unit-normalised dense vectors."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Vector width. Must match the pgvector column definition."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Identifier stored alongside vectors, so a model change is detectable."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per input in order."""

    async def embed_one(self, text: str) -> list[float]:
        """Embed a single text."""
        vectors = await self.embed([text])
        return vectors[0]

    async def close(self) -> None:
        """Release held network resources. No-op unless overridden."""
        return None


def _l2_normalise(vector: list[float]) -> list[float]:
    """Scale to unit length so cosine similarity reduces to a dot product."""
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        return vector
    return [component / norm for component in vector]


class HashingEmbeddingProvider(EmbeddingProvider):
    """Deterministic offline embeddings via the hashing trick.

    Projects token unigrams and bigrams into a fixed-width space using a
    stable hash, weighted by sublinear term frequency. Cosine similarity over
    these vectors behaves like a bag-of-words similarity: exact enough to
    verify that ranking, fusion, filtering and citation logic all work
    end-to-end in CI.
    """

    def __init__(self, dimensions: int = 512) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_name(self) -> str:
        return f"hashing-{self._dimensions}"

    @staticmethod
    def _bucket(term: str, dimensions: int) -> tuple[int, float]:
        """Map a term to a bucket index and a stable +/-1 sign.

        The signed hash keeps collisions from systematically inflating
        similarity, which is the standard correction for the hashing trick.
        """
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        return value % dimensions, 1.0 if (value >> 63) & 1 else -1.0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_sync(text) for text in texts]

    def _embed_sync(self, text: str) -> list[float]:
        tokens = _TOKEN.findall(text.lower())
        if not tokens:
            return [0.0] * self._dimensions

        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        for first, second in pairwise(tokens):
            bigram = f"{first}_{second}"
            counts[bigram] = counts.get(bigram, 0) + 1

        vector = [0.0] * self._dimensions
        for term, count in counts.items():
            index, sign = self._bucket(term, self._dimensions)
            # Sublinear scaling damps the effect of repeated boilerplate.
            vector[index] += sign * (1.0 + math.log(count))
        return _l2_normalise(vector)


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Embeddings via the OpenAI embeddings API."""

    _URL = "https://api.openai.com/v1/embeddings"
    #: Comfortably inside the API's per-request limits.
    _BATCH_SIZE = 96

    def __init__(
        self,
        api_key: str,
        model: str,
        dimensions: int,
        timeout: int,
        max_retries: int = 3,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_name(self) -> str:
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        results: list[list[float]] = []
        for start in range(0, len(texts), self._BATCH_SIZE):
            batch = texts[start : start + self._BATCH_SIZE]
            results.extend(await self._embed_batch(batch))
        return results

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        # The API rejects empty strings; substitute a single space and
        # restore a zero vector afterwards so indices stay aligned.
        sanitised = [text if text.strip() else " " for text in batch]
        body = {
            "model": self._model,
            "input": sanitised,
            "dimensions": self._dimensions,
        }

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await self._client.post(self._URL, json=body)
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "retryable embedding failure",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                payload = response.json()
                break
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_error = exc
                if attempt == self._max_retries:
                    logger.error(
                        "Embedding request failed after %s attempts: %s",
                        attempt,
                        exc,
                    )
                    raise ExternalServiceError(
                        "The embedding provider is unavailable."
                    ) from exc
                backoff = min(2.0**attempt, 10.0)
                logger.warning(
                    "Embedding attempt %s/%s failed (%s); retrying in %.1fs",
                    attempt, self._max_retries, exc, backoff,
                )
                await asyncio.sleep(backoff)
        else:  # pragma: no cover - loop always breaks or raises
            raise ExternalServiceError("Embedding failed.") from last_error

        try:
            ordered = sorted(payload["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in ordered]
        except (KeyError, TypeError) as exc:
            raise ExternalServiceError(
                "Malformed embedding provider response."
            ) from exc

        if len(vectors) != len(batch):
            raise ExternalServiceError(
                f"Embedding count mismatch: sent {len(batch)}, "
                f"received {len(vectors)}."
            )
        return [
            [0.0] * self._dimensions if not original.strip() else _l2_normalise(vec)
            for original, vec in zip(batch, vectors, strict=True)
        ]

    async def close(self) -> None:
        await self._client.aclose()


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    """Return the configured provider, falling back to offline hashing."""
    if settings.EMBEDDING_PROVIDER == "openai":
        if not settings.OPENAI_API_KEY:
            logger.warning(
                "EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is unset; "
                "falling back to deterministic hashing embeddings. "
                "Retrieval quality will be lexical only."
            )
            return HashingEmbeddingProvider(settings.EMBEDDING_DIMENSIONS)
        return OpenAIEmbeddingProvider(
            api_key=settings.OPENAI_API_KEY,
            model=settings.EMBEDDING_MODEL,
            dimensions=settings.EMBEDDING_DIMENSIONS,
            timeout=settings.EMBEDDING_TIMEOUT_SECONDS,
        )
    return HashingEmbeddingProvider(settings.EMBEDDING_DIMENSIONS)


async def close_embedding_provider() -> None:
    """Close the cached provider during application shutdown."""
    provider = get_embedding_provider()
    await provider.close()
    get_embedding_provider.cache_clear()
