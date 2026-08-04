"""Blizzard collections integration for the Collection Tracker.

Reads account-wide collection counts (mounts, pets, toys, titles, tabards,
appearances, transmogs) to enrich the tracker with live progress. Failures
degrade gracefully — the seed catalog still produces a full plan.
"""

from __future__ import annotations

from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.integrations.blizzard.client import BlizzardAPIClient
from app.integrations.blizzard.constants import Endpoints, Namespace

logger = get_logger(__name__)

# endpoint -> category key used by the tracker
_COLLECTION_ENDPOINTS: dict[str, str] = {
    "mounts": Endpoints.ACCOUNT_MOUNTS,
    "pets": Endpoints.ACCOUNT_PETS,
    "toys": Endpoints.ACCOUNT_TOYS,
    "titles": Endpoints.ACCOUNT_TITLES,
    "tabards": Endpoints.ACCOUNT_TABARDS,
    "appearances": Endpoints.ACCOUNT_APPEARANCES,
    "transmog": Endpoints.ACCOUNT_TRANSMOGS,
}


class CollectionsClient:
    """Thin wrapper over Blizzard account-collection endpoints."""

    def __init__(self, client: BlizzardAPIClient | None = None) -> None:
        self._client = client or BlizzardAPIClient()

    async def counts(self) -> dict[str, int]:
        """Return collected counts per category; empty on any failure."""
        out: dict[str, int] = {}
        for key, path in _COLLECTION_ENDPOINTS.items():
            try:
                data = await self._client.get(
                    path, namespace=Namespace.PROFILE, cache_ttl=900
                )
            except ExternalServiceError as exc:
                logger.warning("Collections %s unavailable: %s", key, exc.message)
                continue
            if not data:
                continue
            # Blizzard returns { "mounts": [ { "id", "name" }, ... ] } etc.
            out[key] = len(data.get(key, data.get("items", [])))
        return out

    async def close(self) -> None:
        await self._client.close()
