"""Raider.IO connector — live progression and Mythic+ data.

Raider.IO's Terms of Use prohibit systematically retrieving site content to
compile a database, and prohibit automated access — but both prohibitions
carve out "our publicly available API". This connector therefore uses only the
documented API at ``raider.io/api``, never site HTML.

Only structured facts are retained (scores, ranks, progression state), which
is what the ``STRUCTURED_FACTS`` tier permits. This is also live player data
rather than static game knowledge, so it is refreshed far more often than the
rest of the index.

Commercial deployments require prior written permission from RaiderIO, Inc.;
see the ``commercial_use`` note on the policy entry.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Any

from app.core.logging import get_logger
from app.knowledge.connectors.base import BaseConnector, register_connector
from app.knowledge.documents import EntityType, KnowledgeDocument
from app.knowledge.policy import SourceKey

logger = get_logger(__name__)

__all__ = ["RaiderIOConnector"]

_API_BASE = "https://raider.io/api/v1"


@register_connector
class RaiderIOConnector(BaseConnector):
    """Ingests static Mythic+ metadata and, optionally, character profiles."""

    source = SourceKey.RAIDER_IO

    def __init__(
        self,
        *,
        region: str = "us",
        expansion_id: int = 10,
        characters: Iterable[tuple[str, str]] | None = None,
        client=None,
    ) -> None:
        """
        Args:
            region: Blizzard region code.
            expansion_id: Raider.IO expansion identifier for dungeon listings.
            characters: Optional ``(realm, name)`` pairs to index profiles for.
                Left empty by default — indexing arbitrary players' data is
                neither useful for general questions nor respectful of it.
        """
        super().__init__(client=client)
        self._region = region
        self._expansion_id = expansion_id
        self._characters = list(characters or [])

    async def _mythic_plus_dungeons(self) -> list[dict[str, Any]]:
        """The current season's Mythic+ dungeon pool."""
        payload = await self.get_json(
            f"{_API_BASE}/mythic-plus/static-data",
            params={"expansion_id": self._expansion_id},
        )
        return payload.get("dungeons", []) if payload else []

    async def _character_profile(
        self, realm: str, name: str
    ) -> dict[str, Any] | None:
        try:
            return await self.get_json(
                f"{_API_BASE}/characters/profile",
                params={
                    "region": self._region,
                    "realm": realm,
                    "name": name,
                    "fields": (
                        "mythic_plus_scores_by_season:current,"
                        "raid_progression,gear"
                    ),
                },
            )
        except Exception:
            logger.warning(
                "Raider.IO: could not fetch %s-%s", realm, name, exc_info=True
            )
            return None

    async def fetch(self) -> AsyncIterator[KnowledgeDocument]:
        for dungeon in await self._mythic_plus_dungeons():
            name = dungeon.get("name")
            if not name:
                continue
            yield KnowledgeDocument(
                source=self.source,
                source_id=f"dungeon:{dungeon.get('slug', name)}",
                entity_type=EntityType.DUNGEON,
                title=f"{name} (Mythic+)",
                url=f"https://raider.io/mythic-plus/dungeon/{dungeon.get('slug', '')}",
                facts={
                    "dungeon": name,
                    "short_name": dungeon.get("short_name"),
                    "challenge_mode_id": dungeon.get("challenge_mode_id"),
                    "keystone_timer_seconds": (
                        dungeon.get("keystone_timer_ms", 0) // 1000 or None
                    ),
                    "expansion_id": self._expansion_id,
                },
            )

        for realm, name in self._characters:
            profile = await self._character_profile(realm, name)
            if not profile:
                continue

            scores = profile.get("mythic_plus_scores_by_season") or []
            current_score = None
            if scores:
                current_score = scores[0].get("scores", {}).get("all")

            yield KnowledgeDocument(
                source=self.source,
                source_id=f"character:{self._region}:{realm}:{name}".lower(),
                entity_type=EntityType.CLASS_SPEC,
                title=(
                    f"{profile.get('name', name)} \u2014 "
                    f"{profile.get('realm', realm)}"
                ),
                url=profile.get("profile_url", ""),
                facts={
                    "character_class": profile.get("class"),
                    "active_spec": profile.get("active_spec_name"),
                    "faction": profile.get("faction"),
                    "item_level": profile.get("gear", {}).get("item_level_equipped"),
                    "mythic_plus_score": current_score,
                    "raid_progression": profile.get("raid_progression"),
                    "region": self._region,
                },
            )
