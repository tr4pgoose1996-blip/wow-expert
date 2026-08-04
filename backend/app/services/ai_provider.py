"""AI provider abstraction.

The application depends only on :class:`AIProvider`. Swapping vendors, or
running without one, is a configuration change rather than a code change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import lru_cache

import httpx

from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(slots=True)
class Completion:
    content: str
    model: str
    tokens_used: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


class AIProvider(ABC):
    """Minimal chat-completion interface."""

    @abstractmethod
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.4,
        max_tokens: int = 800,
    ) -> Completion:
        """Generate a completion for the supplied conversation."""

    async def close(self) -> None:  # noqa: B027
        """Release any held network resources.

        Concrete and empty by design: stateless providers need no teardown.
        """


class NullAIProvider(AIProvider):
    """Deterministic offline provider.

    Used in tests and whenever no vendor credentials are configured, so the
    rest of the system — routing, auth, persistence — remains fully testable
    without network access or API spend.
    """

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.4,
        max_tokens: int = 800,
    ) -> Completion:
        question = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )
        return Completion(
            content=(
                "AI guidance is not configured on this deployment. "
                "Set AI_PROVIDER=openai and OPENAI_API_KEY to enable live "
                f"coaching. Received question: {question[:200]}"
            ),
            model="null",
            metadata={"provider": "null"},
        )


class OpenAIProvider(AIProvider):
    """Chat completions via the OpenAI HTTP API."""

    _URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str, model: str, timeout: int) -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.4,
        max_tokens: int = 800,
    ) -> Completion:
        body = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            response = await self._client.post(self._URL, json=body)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise ExternalServiceError("The AI provider timed out.") from exc
        except httpx.HTTPStatusError as exc:
            logger.error(
                "AI provider returned %s: %s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            raise ExternalServiceError("The AI provider rejected the request.") from exc
        except httpx.HTTPError as exc:
            raise ExternalServiceError("Could not reach the AI provider.") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ExternalServiceError("Malformed AI provider response.") from exc

        return Completion(
            content=content.strip(),
            model=data.get("model", self._model),
            tokens_used=data.get("usage", {}).get("total_tokens", 0),
            metadata={"provider": "openai"},
        )

    async def close(self) -> None:
        await self._client.aclose()


@lru_cache
def get_ai_provider() -> AIProvider:
    """Return the configured provider, falling back to the null provider."""
    if settings.AI_PROVIDER == "openai":
        if not settings.OPENAI_API_KEY:
            logger.warning(
                "AI_PROVIDER=openai but OPENAI_API_KEY is unset; "
                "falling back to the null provider."
            )
            return NullAIProvider()
        return OpenAIProvider(
            api_key=settings.OPENAI_API_KEY,
            model=settings.AI_MODEL,
            timeout=settings.AI_TIMEOUT_SECONDS,
        )
    return NullAIProvider()


async def close_ai_provider() -> None:
    """Close the cached provider during shutdown."""
    provider = get_ai_provider()
    await provider.close()
    get_ai_provider.cache_clear()
