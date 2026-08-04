"""Collection Tracker endpoints: plan, fastest goals, completion ETA."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import CurrentUser
from app.modules.collections.schemas import CollectionPlanOut, PlanRequest
from app.modules.collections.service import CollectionTrackerService

router = APIRouter(tags=["collections"])
service = CollectionTrackerService()


@router.post("/plan", response_model=CollectionPlanOut)
async def plan(
    user: CurrentUser,
    request: PlanRequest,
) -> CollectionPlanOut:
    """Build a collection plan for a spec: fastest-obtainable goals, per-category
    completion-time estimates, and class/spec filtering (e.g. Druid forms).

    Pass ``owned`` (category -> collected ids) for a live remaining-ETA; omit it
    for a from-scratch estimate.
    """
    return CollectionPlanOut(**service.plan(request))


@router.get("/counts", response_model=dict[str, int])
async def counts(user: CurrentUser) -> dict[str, int]:
    """Live collected counts per category from Blizzard (empty without creds)."""
    return await service.counts()
