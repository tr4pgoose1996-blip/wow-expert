"""Blizzard Game Data connector — the authoritative knowledge source.

Every fact Blizzard publishes about the game is, by definition, correct about
the game. Where this connector and a community source disagree, the confidence
model prefers this one.

Uses the existing :class:`BlizzardAPIClient` rather than raw HTTP, so token
management, retries, rate limiting and caching are shared with the rest of the
integration instead of reimplemented here.

Coverage is driven by the static Game Data index endpoints, which enumerate
complete sets: every mount, every pet, every achievement, every profession,
every dungeon and raid encounter, every item class, every quest.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any

from app.core.logging import get_logger
from app.integrations.blizzard.client import BlizzardAPIClient
from app.integrations.blizzard.constants import Endpoints, Namespace
from app.knowledge.connectors.base import BaseConnector, register_connector
from app.knowledge.documents import EntityType, KnowledgeDocument
from app.knowledge.policy import SourceKey

logger = get_logger(__name__)

__all__ = ["GAME_DATA_SPECS", "BlizzardGameDataConnector", "GameDataSpec"]

#: Index endpoints not already present on the shared Endpoints class. Kept
#: here because they are knowledge-engine specific rather than part of the
#: character-sync integration surface.
_QUEST_INDEX = "/data/wow/quest/index"
_QUEST_AREA_INDEX = "/data/wow/quest/area/index"
_JOURNAL_INSTANCE_INDEX = "/data/wow/journal-instance/index"
_JOURNAL_ENCOUNTER_INDEX = "/data/wow/journal-encounter/index"
_ITEM_CLASS_INDEX = "/data/wow/item-class/index"
_REPUTATION_FACTION_INDEX = "/data/wow/reputation-faction/index"
_CREATURE_FAMILY_INDEX = "/data/wow/creature-family/index"
_TITLE_INDEX = "/data/wow/title/index"


def _localised(value: Any, locale: str = "en_US") -> str:
    """Extract a display string from Blizzard's localised-name shape.

    Blizzard returns either a bare string or a ``{"en_US": "...", ...}`` map
    depending on whether a locale query parameter was supplied. Handling both
    keeps the connector robust to that inconsistency.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get(locale) or next(iter(value.values()), ""))
    return str(value)


def _strip_links(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove HATEOAS ``key``/``_links`` noise before storing as facts."""
    return {
        key: value
        for key, value in payload.items()
        if key not in {"_links", "key"} and value not in (None, "", [], {})
    }


class GameDataSpec:
    """Describes how to enumerate and render one game-data entity type."""

    __slots__ = (
        "describe",
        "detail_path",
        "entity_type",
        "index_key",
        "index_path",
        "namespace",
        "web_url",
    )

    def __init__(
        self,
        entity_type: EntityType,
        index_path: str,
        index_key: str,
        detail_path: Callable[[int], str],
        *,
        namespace: Namespace = Namespace.STATIC,
        describe: Callable[[dict[str, Any]], str] | None = None,
        web_url: Callable[[int], str] | None = None,
    ) -> None:
        self.entity_type = entity_type
        self.index_path = index_path
        self.index_key = index_key
        self.detail_path = detail_path
        self.namespace = namespace
        self.describe = describe or (lambda _: "")
        self.web_url = web_url or (lambda _: "")


def _describe_mount(data: dict[str, Any]) -> str:
    parts = []
    if desc := _localised(data.get("description")):
        parts.append(desc)
    if source := data.get("source"):
        parts.append(f"Source: {_localised(source.get('name'))}.")
    if faction := data.get("faction"):
        parts.append(f"Faction: {_localised(faction.get('name'))}.")
    if data.get("should_exclude_if_uncollected"):
        parts.append("Not obtainable through normal play.")
    return " ".join(parts)


def _describe_pet(data: dict[str, Any]) -> str:
    parts = []
    if desc := _localised(data.get("description")):
        parts.append(desc)
    if battle_pet := data.get("battle_pet_type"):
        parts.append(f"Battle pet type: {_localised(battle_pet.get('name'))}.")
    if source := data.get("source"):
        parts.append(f"Source: {_localised(source.get('name'))}.")
    if data.get("is_capturable"):
        parts.append("Can be captured in the wild.")
    if data.get("is_tradable"):
        parts.append("Tradable.")
    return " ".join(parts)


def _describe_achievement(data: dict[str, Any]) -> str:
    parts = []
    if desc := _localised(data.get("description")):
        parts.append(desc)
    if category := data.get("category"):
        parts.append(f"Category: {_localised(category.get('name'))}.")
    if points := data.get("points"):
        parts.append(f"Worth {points} achievement points.")
    criteria = data.get("criteria", {})
    if child := criteria.get("child_criteria"):
        names = [
            _localised(c.get("description"))
            for c in child[:12]
            if c.get("description")
        ]
        if names:
            parts.append("Criteria: " + "; ".join(names) + ".")
    return " ".join(parts)


def _describe_quest(data: dict[str, Any]) -> str:
    parts = []
    if desc := _localised(data.get("description")):
        parts.append(desc)
    requirements = data.get("requirements") or {}
    if level := requirements.get("min_character_level"):
        parts.append(f"Minimum level {level}.")
    if area := data.get("area"):
        parts.append(f"Area: {_localised(area.get('name'))}.")
    if rewards := data.get("rewards"):
        if money := rewards.get("money", {}).get("value"):
            parts.append(f"Rewards {money // 10000} gold.")
        if experience := rewards.get("experience"):
            parts.append(f"Rewards {experience} experience.")
    return " ".join(parts)


def _describe_encounter(data: dict[str, Any]) -> str:
    parts = []
    if desc := _localised(data.get("description")):
        parts.append(desc)
    if instance := data.get("instance"):
        parts.append(f"Found in {_localised(instance.get('name'))}.")
    for section in data.get("sections", [])[:8]:
        title = _localised(section.get("title"))
        body = _localised(section.get("body_text"))
        if title and body:
            parts.append(f"\n\n== {title} ==\n{body}")
        elif body:
            parts.append(body)
    return " ".join(parts)


def _describe_instance(data: dict[str, Any]) -> str:
    parts = []
    if desc := _localised(data.get("description")):
        parts.append(desc)
    if location := data.get("location"):
        parts.append(f"Location: {_localised(location.get('name'))}.")
    if expansion := data.get("expansion"):
        parts.append(f"Expansion: {_localised(expansion.get('name'))}.")
    if modes := data.get("modes"):
        names = [_localised(m.get("mode", {}).get("name")) for m in modes]
        parts.append("Difficulties: " + ", ".join(n for n in names if n) + ".")
    if encounters := data.get("encounters"):
        names = [_localised(e.get("name")) for e in encounters]
        parts.append("Encounters: " + ", ".join(n for n in names if n) + ".")
    return " ".join(parts)


def _describe_profession(data: dict[str, Any]) -> str:
    parts = []
    if desc := _localised(data.get("description")):
        parts.append(desc)
    if data.get("type"):
        parts.append(f"Type: {_localised(data['type'].get('name'))}.")
    if skill_tiers := data.get("skill_tiers"):
        names = [_localised(t.get("name")) for t in skill_tiers]
        parts.append("Skill tiers: " + ", ".join(n for n in names if n) + ".")
    return " ".join(parts)


def _describe_faction(data: dict[str, Any]) -> str:
    parts = []
    if desc := _localised(data.get("description")):
        parts.append(desc)
    if group := data.get("reputation_tiers"):
        parts.append(f"Reputation tiers: {_localised(group.get('name'))}.")
    if data.get("is_paragon"):
        parts.append("Supports paragon reputation.")
    return " ".join(parts)


#: The full coverage set. Each entry makes one entity class answerable.
GAME_DATA_SPECS: tuple[GameDataSpec, ...] = (
    GameDataSpec(
        EntityType.MOUNT, Endpoints.MOUNT_INDEX, "mounts",
        Endpoints.mount, describe=_describe_mount,
    ),
    GameDataSpec(
        EntityType.PET, Endpoints.PET_INDEX, "pets",
        Endpoints.pet, describe=_describe_pet,
    ),
    GameDataSpec(
        EntityType.ACHIEVEMENT, Endpoints.ACHIEVEMENT_INDEX, "achievements",
        Endpoints.achievement, describe=_describe_achievement,
    ),
    GameDataSpec(
        EntityType.PROFESSION, Endpoints.PROFESSION_INDEX, "professions",
        lambda i: f"/data/wow/profession/{i}", describe=_describe_profession,
    ),
    GameDataSpec(
        EntityType.QUEST, _QUEST_INDEX, "quests",
        lambda i: f"/data/wow/quest/{i}", describe=_describe_quest,
    ),
    GameDataSpec(
        EntityType.RAID, _JOURNAL_INSTANCE_INDEX, "instances",
        lambda i: f"/data/wow/journal-instance/{i}",
        describe=_describe_instance,
    ),
    GameDataSpec(
        EntityType.BOSS, _JOURNAL_ENCOUNTER_INDEX, "encounters",
        lambda i: f"/data/wow/journal-encounter/{i}",
        describe=_describe_encounter,
    ),
    GameDataSpec(
        EntityType.REPUTATION, _REPUTATION_FACTION_INDEX, "factions",
        lambda i: f"/data/wow/reputation-faction/{i}",
        describe=_describe_faction,
    ),
)


@register_connector
class BlizzardGameDataConnector(BaseConnector):
    """Streams static game data from Blizzard's Game Data APIs."""

    source = SourceKey.BLIZZARD_GAME_DATA

    #: Detail requests issued concurrently. The shared client already enforces
    #: Blizzard's 100 req/s ceiling; this bounds memory and connection count.
    _CONCURRENCY = 12

    def __init__(
        self,
        api_client: BlizzardAPIClient,
        specs: Iterable[GameDataSpec] | None = None,
        *,
        limit_per_type: int | None = None,
    ) -> None:
        # Deliberately skips BaseConnector.__init__'s httpx setup: this
        # connector delegates all transport to the shared Blizzard client.
        from app.knowledge.policy import get_policy

        self.policy = get_policy(self.source)
        self._api = api_client
        self._specs = tuple(specs) if specs else GAME_DATA_SPECS
        self._limit = limit_per_type
        self._owns_client = False

    async def _index_ids(self, spec: GameDataSpec) -> list[int]:
        """Enumerate every entity id for a spec."""
        payload = await self._api.get(
            spec.index_path,
            namespace=spec.namespace,
            cache_ttl=86_400,
        )
        if not payload:
            logger.warning(
                "Blizzard index %s returned nothing; skipping %s",
                spec.index_path, spec.entity_type.value,
            )
            return []

        entries = payload.get(spec.index_key) or []
        ids = [entry["id"] for entry in entries if "id" in entry]
        if self._limit:
            ids = ids[: self._limit]
        return ids

    async def _fetch_detail(
        self, spec: GameDataSpec, entity_id: int
    ) -> dict[str, Any] | None:
        try:
            return await self._api.get(
                spec.detail_path(entity_id),
                namespace=spec.namespace,
                cache_ttl=86_400,
            )
        except Exception:
            logger.warning(
                "Blizzard: failed to fetch %s %s",
                spec.entity_type.value, entity_id, exc_info=True,
            )
            return None

    async def fetch(self) -> AsyncIterator[KnowledgeDocument]:
        """Yield a document for every entity across all configured specs."""
        for spec in self._specs:
            ids = await self._index_ids(spec)
            logger.info(
                "Blizzard: ingesting %d %s records",
                len(ids), spec.entity_type.value,
            )

            semaphore = asyncio.Semaphore(self._CONCURRENCY)

            async def bounded(
                entity_id: int,
                s: GameDataSpec = spec,
                sem: asyncio.Semaphore = semaphore,
            ):
                async with sem:
                    return await self._fetch_detail(s, entity_id)

            # Process in windows so a large index streams rather than
            # buffering every detail response in memory at once.
            window = self._CONCURRENCY * 8
            for start in range(0, len(ids), window):
                batch = ids[start : start + window]
                results = await asyncio.gather(
                    *(bounded(entity_id) for entity_id in batch)
                )
                for entity_id, data in zip(batch, results, strict=True):
                    if not data:
                        continue
                    document = self._to_document(spec, entity_id, data)
                    if document is not None:
                        yield document

    def _to_document(
        self, spec: GameDataSpec, entity_id: int, data: dict[str, Any]
    ) -> KnowledgeDocument | None:
        """Render one API payload as a knowledge document."""
        title = _localised(data.get("name"))
        if not title:
            return None

        # Journal instances split into raids and dungeons only at the record
        # level, so the spec's nominal type is refined here.
        entity_type = spec.entity_type
        if entity_type is EntityType.RAID:
            category = _localised(data.get("category", {}).get("type"))
            if category.upper() == "DUNGEON":
                entity_type = EntityType.DUNGEON

        facts = _strip_links(
            {
                "id": entity_id,
                **{
                    key: (
                        _localised(value.get("name"))
                        if isinstance(value, dict) and "name" in value
                        else value
                    )
                    for key, value in data.items()
                    if key
                    in {
                        "points", "faction", "type", "category", "expansion",
                        "location", "quality", "level", "required_level",
                        "is_capturable", "is_tradable", "is_battlepet",
                        "source", "battle_pet_type",
                    }
                },
            }
        )

        try:
            return KnowledgeDocument(
                source=self.source,
                source_id=str(entity_id),
                entity_type=entity_type,
                title=title,
                url=f"https://develop.battle.net/documentation/world-of-warcraft/game-data-apis#{entity_type.value}-{entity_id}",
                body=spec.describe(data),
                facts=facts,
            )
        except ValueError:
            return None

    async def close(self) -> None:
        """No-op: the Blizzard client is owned by the application, not us."""
