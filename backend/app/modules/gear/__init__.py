"""Gear Advisor feature module.

Simulates secondary stats, compares upgrades, and recommends enchants, gems,
trinkets, crafted gear, and Great Vault priorities for every class and
specialization. Stat weights are keyed by spec, so a single engine serves all
40 specializations.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.logging import get_logger
from app.modules.gear.endpoints import router as gear_router
from app.modules.gear.service import GearAdvisorService
from app.modules.registry import FeatureModule

logger = get_logger(__name__)

MODULE_NAME = "gear"


class GearAdvisorModule(FeatureModule):
    name = MODULE_NAME
    description = (
        "Gear Advisor: simulate stats, compare upgrades, recommend enchants, "
        "gems, trinkets, crafted gear, and Vault choices for every class and spec."
    )

    def __init__(self) -> None:
        self._service = GearAdvisorService()

    @property
    def router(self) -> APIRouter:
        return gear_router

    async def health(self) -> dict[str, str]:
        return {"status": "ok", "service": "gear-advisor"}


from app.modules.registry import registry as _registry  # noqa: E402

_registry.register(GearAdvisorModule())
