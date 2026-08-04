"""Combat Coach endpoints: browse instances/bosses and get role coaching."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.dependencies import CurrentUser
from app.modules.coach.domain import Instance
from app.modules.coach.schemas import (
    BossOut,
    CoachBriefingOut,
    CoachBriefingRequest,
    InstanceOut,
)
from app.modules.coach.service import CombatCoachService

router = APIRouter(tags=["coach"])
service = CombatCoachService()


def _to_instance_out(inst: Instance) -> InstanceOut:
    return InstanceOut(
        journal_id=inst.journal_id,
        name=inst.name,
        content_type=inst.content_type.value,
        boss_count=len(inst.bosses),
        notable_affixes=[a.value for a in inst.notable_affixes],
    )


@router.get("", response_model=list[InstanceOut])
async def list_instances(
    user: CurrentUser,
    content_type: Annotated[str | None, Query(description="dungeon | raid")] = None,
) -> list[InstanceOut]:
    """List dungeons and raids the coach can teach."""
    instances = service.list_instances(content_type)
    return [_to_instance_out(i) for i in instances]


@router.get("/{instance_id}", response_model=InstanceOut)
async def get_instance(
    user: CurrentUser,
    instance_id: Annotated[int, Path(..., description="Blizzard journal-instance id.")],
) -> InstanceOut:
    """Read one instance and its boss count."""
    inst = service.get_instance(instance_id)
    return _to_instance_out(inst)


@router.get("/{instance_id}/bosses", response_model=list[BossOut])
async def list_bosses(
    user: CurrentUser,
    instance_id: Annotated[int, Path(..., description="Blizzard journal-instance id.")],
) -> list[BossOut]:
    """List bosses in an instance."""
    inst = service.get_instance(instance_id)
    return [
        BossOut(
            journal_id=b.journal_id,
            name=b.name,
            instance_id=b.instance_id,
            summary=b.summary,
            mechanic_count=len(b.mechanics),
            common_mistakes=list(b.common_mistakes),
        )
        for b in inst.bosses
    ]


@router.post("/{instance_id}/boss/{boss_id}/brief", response_model=CoachBriefingOut)
async def brief(
    user: CurrentUser,
    instance_id: Annotated[int, Path(..., description="Blizzard journal-instance id.")],
    boss_id: Annotated[int, Path(..., description="Blizzard journal-encounter id.")],
    request: CoachBriefingRequest,
) -> CoachBriefingOut:
    """Generate a role/difficulty/affix-specific coaching briefing for a boss.

    Covers tank, healer, and DPS mechanics, interrupts, movement, cooldown
    timing, burst windows, defensive planning, and common mistakes across
    Normal, Heroic, Mythic, and Mythic+.
    """
    briefing = await service.brief(instance_id, boss_id, request)
    return CoachBriefingOut.from_briefing(briefing)
