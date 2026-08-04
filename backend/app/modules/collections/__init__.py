"""Collection Tracker feature module.

Tracks mounts, hunter pets, battle pets, toys, appearances, achievements,
titles, tabards, and druid forms. Recommends the fastest-obtainable
collectibles, estimates completion time, and filters by class/specialization.
Live Blizzard collection counts enrich progress; the seed catalog keeps every
plan working offline.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.logging import get_logger
from app.modules.collections.endpoints import router as collections_router
from app.modules.collections.service import CollectionTrackerService
from app.modules.registry import FeatureModule

logger = get_logger(__name__)

MODULE_NAME = "collections"


class CollectionTrackerModule(FeatureModule):
    name = MODULE_NAME
    description = (
        "Collection Tracker: mounts, hunter/battle pets, toys, appearances, "
        "achievements, titles, tabards, druid forms — fastest-obtainable goals, "
        "completion ETAs, and class/spec filtering."
    )

    def __init__(self) -> None:
        self._service = CollectionTrackerService()

    @property
    def router(self) -> APIRouter:
        return collections_router

    async def health(self) -> dict[str, str]:
        return {"status": "ok", "service": "collection-tracker"}


from app.modules.registry import registry as _registry  # noqa: E402

_registry.register(CollectionTrackerModule())
