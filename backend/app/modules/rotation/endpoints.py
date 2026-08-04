"""Rotation advisor endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.dependencies import CurrentUser
from app.core.exceptions import NotFoundError, ValidationError
from app.modules.rotation.schemas import (
    AbilityOut,
    RotationAdviceOut,
    RotationListOut,
    RotationProfileOut,
    RotationRequest,
    SpecializationOut,
    to_advice_out,
)
from app.modules.rotation.service import RotationService

router = APIRouter(tags=["rotation"])
service = RotationService()


@router.get("", response_model=RotationListOut)
async def list_specs(user: CurrentUser) -> RotationListOut:
    """List every supported specialization with its Blizzard id."""
    return RotationListOut(
        total=len(service.list_specializations()),
        specializations=[
            SpecializationOut.from_spec(s) for s in service.list_specializations()
        ],
    )


@router.get("/{spec_id}", response_model=RotationProfileOut)
async def profile(
    user: CurrentUser,
    spec_id: Annotated[int, Path(..., description="Blizzard specialization id.")],
) -> RotationProfileOut:
    """Read the rotation profile (cooldowns, talent/gear/trinket notes)."""
    try:
        prof = service.get_profile(spec_id)
    except ValidationError as exc:
        raise NotFoundError(exc.message, details=exc.details) from exc
    return RotationProfileOut.from_profile(prof)


@router.post("/advise", response_model=RotationAdviceOut)
async def advise(
    user: CurrentUser,
    request: RotationRequest,
) -> RotationAdviceOut:
    """Compute the next suggested ability, a full rotation, or a cooldown plan.

    Pass ``state`` to get live "what do I press now?" advice; omit it for the
    static priority list for a ``situation`` (opener, single_target, aoe,
    movement, cooldowns, defensive).
    """
    advice = service.advise(request)
    return to_advice_out(advice)


@router.get("/{spec_id}/rotation", response_model=list[AbilityOut])
async def rotation(
    user: CurrentUser,
    spec_id: Annotated[int, Path(..., description="Blizzard specialization id.")],
    situation: Annotated[
        str,
        Query(
            pattern="^(opener|single_target|aoe|movement|defensive)$",
            description="Which priority list to return.",
        ),
    ] = "single_target",
) -> list[AbilityOut]:
    """Return an ordered rotation list for a situation (teaching view)."""
    from app.modules.rotation.engine import AdviceKind

    request = RotationRequest(spec_id=spec_id, situation=AdviceKind(situation))
    advice = service.advise(request)
    out: list[AbilityOut] = []
    if advice.suggestion:
        out.append(AbilityOut(**advice.suggestion.to_dict()))
    out.extend(AbilityOut(**a.to_dict()) for a in advice.alternatives)
    return out
