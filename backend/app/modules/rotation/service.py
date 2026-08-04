"""Rotation advisor service.

Thin layer that maps API inputs to the engine and resolves specialization
metadata. The engine itself is stateless and lives in :mod:`engine`.
"""

from __future__ import annotations

from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.modules.rotation.engine import (
    RotationAdvice,
    RotationAdvisor,
)
from app.modules.rotation.profiles import PROFILE_BY_ID
from app.modules.rotation.schemas import (
    AdviceKind,
    RotationRequest,
)
from app.modules.rotation.specs import (
    SPECIALIZATIONS,
    CombatState,
    Specialization,
)

logger = get_logger(__name__)


class RotationService:
    """Owns the advisor and exposes the operations the API needs."""

    def __init__(self) -> None:
        self._advisor = RotationAdvisor()

    # -- catalog -------------------------------------------------------------

    def list_specializations(self) -> list[Specialization]:
        return list(SPECIALIZATIONS)

    def get_profile(self, spec_id: int):
        if spec_id not in PROFILE_BY_ID:
            raise ValidationError(
                f"No rotation profile for specialization id {spec_id}.",
                details={"known_spec_ids": sorted(PROFILE_BY_ID)},
            )
        return PROFILE_BY_ID[spec_id]

    # -- advice --------------------------------------------------------------

    def advise(self, request: RotationRequest) -> RotationAdvice:
        if request.spec_id not in PROFILE_BY_ID:
            raise ValidationError(
                f"No rotation profile for specialization id {request.spec_id}.",
                details={"known_spec_ids": sorted(PROFILE_BY_ID)},
            )

        state = self._to_state(request)
        advisor = RotationAdvisor(active_talents=set(state.active_buffs or ()))
        # active_talents is passed via CombatStateIn for talent awareness.
        if request.state is not None:
            advisor.active_talents = set(request.state.active_talents)

        if request.situation == AdviceKind.NEXT:
            return advisor.advise(
                request.spec_id,
                state,
                allow_cooldowns=request.allow_cooldowns,
                allow_defensives=request.allow_defensives,
            )

        # Static views.
        from app.modules.rotation.engine import AdviceKind as EngineAdvice

        kind_map = {
            AdviceKind.OPENER: EngineAdvice.OPENER,
            AdviceKind.SINGLE_TARGET: EngineAdvice.SINGLE_TARGET,
            AdviceKind.AOE: EngineAdvice.AOE,
            AdviceKind.MOVEMENT: EngineAdvice.MOVEMENT,
            AdviceKind.DEFENSIVE: EngineAdvice.DEFENSIVE,
        }
        if request.situation in kind_map:
            suggestions = advisor.rotation_for(
                request.spec_id, kind_map[request.situation], state
            )
            profile = advisor.get_profile(request.spec_id)
            return RotationAdvice(
                spec_id=profile.spec_id,
                spec_name=profile.spec_name,
                wow_class=profile.wow_class,
                role=profile.role,
                situation=request.situation.value,
                target_health_pct=state.target_health_pct,
                targets=state.targets,
                resource=state.resource,
                resource_max=state.resource_max,
                resource_type=profile.resource.value,
                moving=state.moving,
                burst_active=state.burst_active,
                suggestion=suggestions[0] if suggestions else None,
                alternatives=suggestions[1:],
                ready_cooldowns=[],
                defensives=[a.name for a in profile.defensives],
                talent_hints=advisor._talent_hints(profile),
                gear_hint=profile.gear_guidance,
                trinket_hint=profile.trinket_guidance,
                notes=profile.notes,
                warnings=[],
            )

        if request.situation == AdviceKind.COOLDOWNS:
            plan = advisor.cooldown_plan(request.spec_id, state)
            profile = advisor.get_profile(request.spec_id)
            return RotationAdvice(
                spec_id=profile.spec_id,
                spec_name=profile.spec_name,
                wow_class=profile.wow_class,
                role=profile.role,
                situation="cooldowns",
                target_health_pct=state.target_health_pct,
                targets=state.targets,
                resource=state.resource,
                resource_max=state.resource_max,
                resource_type=profile.resource.value,
                moving=state.moving,
                burst_active=state.burst_active,
                suggestion=None,
                alternatives=[],
                ready_cooldowns=plan["ready"],
                defensives=[],
                talent_hints=[],
                gear_hint=profile.gear_guidance,
                trinket_hint=profile.trinket_guidance,
                notes=profile.notes,
                warnings=[],
            )

        raise ValidationError(f"Unknown situation {request.situation.value}.")

    # -- mapping -------------------------------------------------------------

    def _to_state(self, request: RotationRequest) -> CombatState:
        if request.state is None:
            return CombatState(spec_id=request.spec_id)
        s = request.state
        return CombatState(
            spec_id=request.spec_id,
            target_health_pct=s.target_health_pct,
            targets=s.targets,
            resource=s.resource,
            resource_max=s.resource_max,
            moving=s.moving,
            time=s.time,
            cooldowns_remaining=dict(s.cooldowns_remaining),
            active_buffs=set(s.active_buffs),
            burst_active=s.burst_active,
            starved=s.starved,
        )
