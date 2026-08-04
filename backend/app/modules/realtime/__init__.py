"""Realtime overlay feature module.

Exposes a WebSocket the transparent desktop overlay connects to. It streams
rotation/cooldown displays plus personalized recommendations (from the reasoning
engine) and broadcasts boss/rare/waypoint alerts from the in-process game bus.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.logging import get_logger
from app.modules.realtime.endpoints import router as realtime_router
from app.modules.registry import FeatureModule

logger = get_logger(__name__)

MODULE_NAME = "realtime"


class RealtimeModule(FeatureModule):
    name = MODULE_NAME
    description = (
        "Realtime overlay WebSocket: streams rotation/cooldown displays, quest "
        "tracking, and boss/rare/waypoint alerts to the transparent desktop overlay."
    )

    @property
    def router(self) -> APIRouter:
        return realtime_router


def register() -> RealtimeModule:
    from app.modules.registry import registry

    module = RealtimeModule()
    return registry.register(module)


module = register()
