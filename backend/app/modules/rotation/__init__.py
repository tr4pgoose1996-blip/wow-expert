"""Rotation Advisor feature module.

Real-time, spec-aware rotation advice: next-action suggestions, cooldown
planning, AoE/single-target/movement/execute logic, and talent/gear/trinket
awareness. The engine is generic over the :class:`RotationProfile` shape, so
all 39 specializations share one code path.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.logging import get_logger
from app.modules.registry import FeatureModule
from app.modules.rotation.endpoints import router as rotation_router
from app.modules.rotation.service import RotationService

logger = get_logger(__name__)

MODULE_NAME = "rotation"


class RotationModule(FeatureModule):
    name = MODULE_NAME
    description = (
        "Real-time rotation advisor: priority system, cooldown planning, "
        "AoE/single-target/movement/execute logic, and resource/talent/gear/"
        "trinket awareness for every specialization."
    )

    def __init__(self) -> None:
        self._service = RotationService()

    @property
    def router(self) -> APIRouter:
        return rotation_router

    async def health(self) -> dict[str, str]:
        return {"status": "ok", "service": "rotation-advisor"}


# Register on import so app.main picks it up without edits to the factory.
from app.modules.registry import registry as _registry  # noqa: E402

_registry.register(RotationModule())
