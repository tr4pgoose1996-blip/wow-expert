"""Gear Advisor endpoints: simulate stats, compare upgrades, recommend optimisations."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import CurrentUser
from app.modules.gear.schemas import (
    CompareRequest,
    RecommendOut,
    RecommendRequest,
)
from app.modules.gear.service import GearAdvisorService

router = APIRouter(tags=["gear"])
service = GearAdvisorService()


@router.post("/advise", response_model=RecommendOut)
async def advise(
    user: CurrentUser,
    request: RecommendRequest,
) -> RecommendOut:
    """Simulate stats, rank upgrades, and recommend enchants/gems/trinkets/
    crafted gear/vault priorities for a specialization.

    Send ``equipped`` and ``candidates`` item lists (optionally from Blizzard
    equipment data) to get a scored, ranked upgrade plan; omit them for generic
    slot/enchant/gem advice.
    """
    rec = service.recommend(request)
    return RecommendOut(**rec.to_dict())


@router.post("/compare", response_model=dict)
async def compare(
    user: CurrentUser,
    request: CompareRequest,
) -> dict:
    """Score one candidate against the currently equipped item in a slot."""
    return service.compare(request)
