"""Rotation advisor schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.modules.rotation.specs import (
    ResourceType,
    RotationProfile,
    Specialization,
)


class AdviceKind(StrEnum):
    """Which rotation view to render."""

    NEXT = "next"
    OPENER = "opener"
    SINGLE_TARGET = "single_target"
    AOE = "aoe"
    MOVEMENT = "movement"
    COOLDOWNS = "cooldowns"
    DEFENSIVE = "defensive"


class SpecializationOut(BaseModel):
    spec_id: int
    name: str
    wow_class: str
    role: str

    @staticmethod
    def from_spec(spec: Specialization) -> SpecializationOut:
        return SpecializationOut(
            spec_id=spec.spec_id,
            name=spec.name,
            wow_class=spec.wow_class,
            role=spec.role,
        )


class CombatStateIn(BaseModel):
    """Player-supplied combat snapshot the advisor reasons over.

    The specialization is supplied on :class:`RotationRequest`; it is not
    repeated here.
    """

    target_health_pct: float = Field(100.0, ge=0, le=100)
    targets: int = Field(1, ge=1, description="Number of enemies in range.")
    resource: float = Field(0.0, ge=0, description="Current primary resource pool.")
    resource_max: float = Field(100.0, gt=0)
    moving: bool = False
    time: float = Field(0.0, ge=0, description="Seconds since pull.")
    cooldowns_remaining: dict[str, float] = Field(default_factory=dict)
    active_buffs: list[int] = Field(default_factory=list)
    burst_active: bool = False
    starved: bool = False
    active_talents: list[str] = Field(default_factory=list)


class RotationRequest(BaseModel):
    spec_id: int = Field(..., description="Blizzard specialization id.")
    situation: AdviceKind = AdviceKind.NEXT
    state: CombatStateIn | None = None
    allow_cooldowns: bool = True
    allow_defensives: bool = True


class AbilityOut(BaseModel):
    name: str
    kind: str
    spell_id: int | None = None
    reason: str
    priority: int
    cost: float
    cooldown_remaining: float
    castable_while_moving: bool
    weight: float


class CooldownOut(BaseModel):
    name: str
    spell_id: int | None = None
    cooldown: float
    mitigation: float


class RotationProfileOut(BaseModel):
    spec_id: int
    spec_name: str
    wow_class: str
    role: str
    resource: ResourceType
    secondary_resource: str | None = None
    execute_below_pct: float | None = None
    has_aoe: bool
    has_movement_set: bool
    expected_talents: list[str]
    gear_guidance: str
    trinket_guidance: str
    notes: str
    cooldowns: list[CooldownOut]
    ability_count: int

    @staticmethod
    def from_profile(p: RotationProfile) -> RotationProfileOut:
        return RotationProfileOut(
            spec_id=p.spec_id,
            spec_name=p.spec_name,
            wow_class=p.wow_class,
            role=p.role,
            resource=p.resource,
            secondary_resource=p.secondary_resource,
            execute_below_pct=p.execute_below_pct,
            has_aoe=len(p.aoe) > 0,
            has_movement_set=len(p.movement) > 0,
            expected_talents=list(p.expected_talents),
            gear_guidance=p.gear_guidance,
            trinket_guidance=p.trinket_guidance,
            notes=p.notes,
            cooldowns=[
                CooldownOut(
                    name=a.name,
                    spell_id=a.spell_id,
                    cooldown=a.cooldown,
                    mitigation=a.mitigation,
                )
                for a in p.cooldowns
            ],
            ability_count=len(
                set(p.single_target) | set(p.aoe) | set(p.opener) | set(p.movement)
            ),
        )


class RotationAdviceOut(BaseModel):
    spec_id: int
    spec_name: str
    wow_class: str
    role: str
    situation: str
    target_health_pct: float
    targets: int
    resource: float
    resource_max: float
    resource_type: str
    moving: bool
    burst_active: bool
    suggestion: AbilityOut | None = None
    alternatives: list[AbilityOut] = Field(default_factory=list)
    ready_cooldowns: list[str] = Field(default_factory=list)
    defensives: list[str] = Field(default_factory=list)
    talent_hints: list[str] = Field(default_factory=list)
    gear_hint: str = ""
    trinket_hint: str = ""
    notes: str = ""
    warnings: list[str] = Field(default_factory=list)


class RotationListOut(BaseModel):
    total: int
    specializations: list[SpecializationOut]


def to_advice_out(advice: Any) -> RotationAdviceOut:  # type: ignore[name-defined]
    return RotationAdviceOut(**advice.to_dict())
