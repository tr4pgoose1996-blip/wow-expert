"""Reasoning / Game Master feature module.

Hermes reasons like a veteran Game Master: it infers player intent, decomposes
it into ordered objectives sourced from every subsystem (rotation, gear, coach,
collections), ranks them by an efficiency model, and explains *why* each step is
optimal. Player facts are persisted so future reasoning is personalized, and
conversations are remembered across sessions.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.logging import get_logger
from app.modules.reasoning.endpoints import router as reasoning_router
from app.modules.registry import FeatureModule

logger = get_logger(__name__)

MODULE_NAME = "reasoning"


class ReasoningModule(FeatureModule):
    name = MODULE_NAME
    description = (
        "Reasoning Game Master: infers intent, plans progression across every "
        "subsystem, and explains why each recommendation is optimal. Learns the "
        "player over time and remembers conversations."
    )

    @property
    def router(self) -> APIRouter:
        return reasoning_router


def register() -> ReasoningModule:
    """Register the reasoning module with the shared registry."""
    from app.modules.registry import registry

    module = ReasoningModule()
    return registry.register(module)


module = register()
