"""Combat Coach feature module.

Teaches every dungeon, raid, boss, and Mythic+ keystone for tank, healer, and
DPS roles across Heroic, Mythic, and Mythic+. Coaching is generated from
structured encounter facts plus a chosen role/difficulty/affix set, so coverage
scales to all content without per-fight hand-authoring.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.logging import get_logger
from app.modules.coach.endpoints import router as coach_router
from app.modules.coach.service import CombatCoachService
from app.modules.registry import FeatureModule

logger = get_logger(__name__)

MODULE_NAME = "coach"


class CombatCoachModule(FeatureModule):
    name = MODULE_NAME
    description = (
        "Combat Coach: dungeon/raid/boss/Mythic+ teaching for tank, healer, and "
        "DPS — mechanics, interrupts, movement, cooldown timing, burst windows, "
        "defensive planning, and common mistakes across Heroic, Mythic, and Mythic+."
    )

    def __init__(self) -> None:
        self._service = CombatCoachService()

    @property
    def router(self) -> APIRouter:
        return coach_router

    async def health(self) -> dict[str, str]:
        return {"status": "ok", "service": "combat-coach"}


# Register on import so app.main picks it up without edits to the factory.
from app.modules.registry import registry as _registry  # noqa: E402

_registry.register(CombatCoachModule())
