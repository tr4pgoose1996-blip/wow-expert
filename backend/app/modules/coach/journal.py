"""Blizzard Journal integration for the Combat Coach.

Hydrates instance and encounter metadata from Blizzard's Journal API
(``/data/wow/journal-instance/{id}`` and ``/data/wow/journal-encounter/{id}``,
namespace ``static-{region}``). The coach works fully from its own structured
seed data; the Journal is an *enrichment* layer that adds live names, boss
lists, and descriptions when credentials and network are available. Failures
degrade gracefully to the local data so coaching never breaks.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.integrations.blizzard.client import BlizzardAPIClient
from app.integrations.blizzard.constants import Endpoints, Namespace

logger = get_logger(__name__)


class JournalClient:
    """Thin wrapper over the Blizzard Journal endpoints."""

    def __init__(self, client: BlizzardAPIClient | None = None) -> None:
        self._client = client or BlizzardAPIClient()

    async def get_instance(self, instance_id: int) -> dict[str, Any] | None:
        """Return raw journal-instance JSON, or ``None`` on 404/error."""
        try:
            return await self._client.get(
                Endpoints.journal_instance(instance_id),
                namespace=Namespace.STATIC,
                cache_ttl=3600,
            )
        except ExternalServiceError as exc:
            logger.warning(
                "Journal instance %s unavailable: %s", instance_id, exc.message
            )
            return None

    async def get_encounter(self, encounter_id: int) -> dict[str, Any] | None:
        """Return raw journal-encounter JSON, or ``None`` on 404/error."""
        try:
            return await self._client.get(
                Endpoints.journal_encounter(encounter_id),
                namespace=Namespace.STATIC,
                cache_ttl=3600,
            )
        except ExternalServiceError as exc:
            logger.warning(
                "Journal encounter %s unavailable: %s", encounter_id, exc.message
            )
            return None

    async def close(self) -> None:
        await self._client.close()
