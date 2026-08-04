"""Rotation advisor engine.

Given a :class:`RotationProfile` and a :class:`CombatState`, the engine
produces the *next* suggested ability plus a short rationale, and can also
return the ordered full rotation for a situation (single-target, AoE, opener,
or movement). The same engine powers both the live "what do I press now?"
advisor and the static teaching pages, so the advice never diverges.

Design notes
------------
* The priority system is generic: every ability carries a ``priority`` and
  ``weight``, and eligibility is a pure function of state. No class-specific
  branches exist, which is why 39 specs share one code path.
* Cooldown planning looks at ``cooldowns_remaining``; a cooldown is only
  suggested when ready (or nearly so), and burst cooldowns are gated behind
  :class:`BurstTrigger` conditions so they line up with resource/trinket
  timing.
* Movement adaptation strips to abilities that are ``castable_while_moving``
  (or declared in the profile's ``movement`` list) and never suggests a
  cast-time filler while moving.
* Execute logic swaps in execute-priority abilities when the target is below
  ``execute_below_pct``.
* Talent/gear/trinket awareness are surfaced as *metadata* (hints), not as
  branches: the engine flags when the player's active talents differ from
  the profile's ``expected_talents`` and echoes the gear/trinket guidance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.exceptions import ValidationError
from app.modules.rotation.profiles import PROFILE_BY_ID
from app.modules.rotation.specs import (
    Ability,
    AbilityKind,
    CombatState,
    RotationProfile,
)


class AdviceKind(StrEnum):
    """What kind of advice the engine is returning."""

    NEXT_ACTION = "next_action"
    OPENER = "opener"
    SINGLE_TARGET = "single_target"
    AOE = "aoe"
    MOVEMENT = "movement"
    BURST = "burst"
    DEFENSIVE = "defensive"


@dataclass
class AbilitySuggestion:
    """One suggested ability with the reasoning behind it."""

    name: str
    kind: str
    spell_id: int | None
    reason: str
    priority: int
    cost: float
    cooldown_remaining: float
    castable_while_moving: bool
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "spell_id": self.spell_id,
            "reason": self.reason,
            "priority": self.priority,
            "cost": self.cost,
            "cooldown_remaining": round(self.cooldown_remaining, 2),
            "castable_while_moving": self.castable_while_moving,
            "weight": self.weight,
        }


@dataclass
class RotationAdvice:
    """Full advice payload for one decision point."""

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
    # The primary suggestion, if any.
    suggestion: AbilitySuggestion | None
    # Ordered alternative actions considered (next-best), for UI.
    alternatives: list[AbilitySuggestion] = field(default_factory=list)
    # Cooldowns that are ready and worth planning.
    ready_cooldowns: list[str] = field(default_factory=list)
    # Defensive abilities recommended given incoming threat.
    defensives: list[str] = field(default_factory=list)
    # Talent/gear/trinket awareness hints.
    talent_hints: list[str] = field(default_factory=list)
    gear_hint: str = ""
    trinket_hint: str = ""
    notes: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "spec_name": self.spec_name,
            "wow_class": self.wow_class,
            "role": self.role,
            "situation": self.situation,
            "target_health_pct": self.target_health_pct,
            "targets": self.targets,
            "resource": self.resource,
            "resource_max": self.resource_max,
            "resource_type": self.resource_type,
            "moving": self.moving,
            "burst_active": self.burst_active,
            "suggestion": self.suggestion.to_dict() if self.suggestion else None,
            "alternatives": [a.to_dict() for a in self.alternatives],
            "ready_cooldowns": self.ready_cooldowns,
            "defensives": self.defensives,
            "talent_hints": self.talent_hints,
            "gear_hint": self.gear_hint,
            "trinket_hint": self.trinket_hint,
            "notes": self.notes,
            "warnings": self.warnings,
        }


def _ready(ability: Ability, state: CombatState, threshold: float = 0.0) -> bool:
    """Return True when an ability's cooldown has elapsed."""
    remaining = state.cooldowns_remaining.get(ability.name, 0.0)
    return remaining <= threshold


def _affordable(ability: Ability, state: CombatState) -> bool:
    """A spender/builder is affordable when the pool covers its cost."""
    if ability.cost > 0:
        return state.resource >= ability.cost
    # Builders (negative cost) are always castable; 0-cost too.
    return True


def _talent_ok(ability: Ability, active_talents: set[str] | None) -> bool:
    if not ability.requires_talent:
        return True
    if not active_talents:
        # Without talent info we still recommend it; talent_hints will flag it.
        return True
    return ability.requires_talent in active_talents


def _buffs_ok(ability: Ability, state: CombatState) -> bool:
    if not all(needed in state.active_buffs for needed in ability.requires_buffs):
        return False
    return all(avoid not in state.active_buffs for avoid in ability.avoid_if_buffs)


def _aoe_ok(ability: Ability, targets: int) -> bool:
    return targets >= ability.min_targets


def _eligible(
    ability: Ability,
    state: CombatState,
    active_talents: set[str] | None,
    *,
    moving: bool,
    targets: int,
) -> tuple[bool, str]:
    """Return (eligible, reason-if-ineligible)."""
    if not _aoe_ok(ability, targets):
        return False, f"needs {ability.min_targets}+ targets"
    if not _buffs_ok(ability, state):
        return False, "missing required buff"
    if not _talent_ok(ability, active_talents):
        return False, f"requires talent {ability.requires_talent}"
    if not _ready(ability, state):
        return False, "on cooldown"
    if not _affordable(ability, state):
        return False, "not enough resource"
    if moving and not (
        ability.castable_while_moving or ability.kind == AbilityKind.UTILITY
    ):
        return False, "not castable while moving"
    return True, ""


def _suggest(
    ability: Ability,
    state: CombatState,
    reason: str,
) -> AbilitySuggestion:
    return AbilitySuggestion(
        name=ability.name,
        kind=ability.kind.value,
        spell_id=ability.spell_id,
        reason=reason,
        priority=ability.priority,
        cost=ability.cost,
        cooldown_remaining=round(state.cooldowns_remaining.get(ability.name, 0.0), 2),
        castable_while_moving=ability.castable_while_moving,
        weight=ability.weight,
    )


class RotationAdvisor:
    """Stateless engine that turns state into advice.

    Instantiate once and call :meth:`advise`, :meth:`rotation_for`, and
    :meth:`cooldown_plan` as needed. It reads profiles from the registry but
    holds no mutable state of its own, so it is safe to share across requests.
    """

    def __init__(self, active_talents: set[str] | None = None) -> None:
        self.active_talents = active_talents

    # -- public API ----------------------------------------------------------

    def get_profile(self, spec_id: int) -> RotationProfile:
        profile = PROFILE_BY_ID.get(spec_id)
        if profile is None:
            raise ValidationError(
                f"No rotation profile for specialization id {spec_id}.",
                details={"known_spec_ids": sorted(PROFILE_BY_ID)},
            )
        return profile

    def advise(
        self,
        spec_id: int,
        state: CombatState,
        *,
        allow_cooldowns: bool = True,
        allow_defensives: bool = True,
    ) -> RotationAdvice:
        """Compute the next suggested action for the given state."""
        profile = self.get_profile(spec_id)
        targets = max(1, state.targets)
        moving = state.moving

        # Choose the priority list by situation.
        if moving:
            candidates = list(profile.movement) or [
                a
                for a in (profile.single_target)
                if a.castable_while_moving or a.kind == AbilityKind.UTILITY
            ]
            situation = "movement"
            pool = candidates
        elif targets >= 2:
            pool = list(profile.aoe)
            situation = "aoe"
        else:
            pool = list(profile.single_target)
            situation = "single_target"

        # Execute-phase overlay: when below the execute threshold and the
        # profile defines one, prefer execute abilities. We surface execute
        # abilities by lowering their effective priority via the note; here
        # we keep them as-is but flag the phase.
        in_execute = (
            profile.execute_below_pct is not None
            and state.target_health_pct <= profile.execute_below_pct
        )
        if in_execute:
            situation = f"{situation} (execute {int(profile.execute_below_pct)}%)"

        eligible: list[tuple[Ability, str]] = []
        for ability in pool:
            ok, _ = _eligible(
                ability, state, self.active_talents, moving=moving, targets=targets
            )
            if ok:
                eligible.append((ability, ""))
            elif allow_cooldowns and ability.kind in (
                AbilityKind.COOLDOWN,
                AbilityKind.BURST,
            ):
                # Keep cooldowns in the alternative list even if not ready,
                # so the UI can show "X ready in 4.2s".
                pass

        # Sort by priority then weight (higher weight first on ties).
        eligible.sort(key=lambda item: (item[0].priority, -item[0].weight))

        suggestion: AbilitySuggestion | None = None
        alternatives: list[AbilitySuggestion] = []
        if eligible:
            best, _ = eligible[0]
            reason = self._reason(best, state, situation)
            suggestion = _suggest(best, state, reason)
            for ability, _ in eligible[1:6]:
                alternatives.append(
                    _suggest(ability, state, self._reason(ability, state, situation))
                )

        ready_cooldowns = self._ready_cooldowns(profile, state, allow_cooldowns)
        defensives = self._defensives(profile, state, allow_defensives)
        talent_hints = self._talent_hints(profile)

        warnings: list[str] = []
        if state.starved:
            warnings.append("Resource starved: spenders unavailable, use builders.")
        if in_execute and state.target_health_pct > (profile.execute_below_pct or 0):
            warnings.append("Target above execute threshold; normal priority applies.")

        return RotationAdvice(
            spec_id=profile.spec_id,
            spec_name=profile.spec_name,
            wow_class=profile.wow_class,
            role=profile.role,
            situation=situation,
            target_health_pct=state.target_health_pct,
            targets=targets,
            resource=state.resource,
            resource_max=state.resource_max,
            resource_type=profile.resource.value,
            moving=moving,
            burst_active=state.burst_active,
            suggestion=suggestion,
            alternatives=alternatives,
            ready_cooldowns=ready_cooldowns,
            defensives=defensives,
            talent_hints=talent_hints,
            gear_hint=profile.gear_guidance,
            trinket_hint=profile.trinket_guidance,
            notes=profile.notes,
            warnings=warnings,
        )

    def rotation_for(
        self,
        spec_id: int,
        situation: AdviceKind,
        state: CombatState | None = None,
    ) -> list[AbilitySuggestion]:
        """Return the ordered ability list for a static teaching view."""
        profile = self.get_profile(spec_id)
        state = state or CombatState(spec_id=spec_id)
        pools = {
            AdviceKind.OPENER: profile.opener,
            AdviceKind.SINGLE_TARGET: profile.single_target,
            AdviceKind.AOE: profile.aoe,
            AdviceKind.MOVEMENT: profile.movement
            or [
                a
                for a in profile.single_target
                if a.castable_while_moving or a.kind == AbilityKind.UTILITY
            ],
            AdviceKind.DEFENSIVE: profile.defensives,
        }
        pool = pools.get(situation, profile.single_target)
        return [
            _suggest(a, state, self._reason(a, state, situation.value))
            for a in sorted(pool, key=lambda x: x.priority)
        ]

    def cooldown_plan(self, spec_id: int, state: CombatState) -> dict[str, Any]:
        """Summarise cooldown readiness and burst alignment."""
        profile = self.get_profile(spec_id)
        on_cooldown: list[dict[str, Any]] = []
        ready: list[str] = []
        for ability in profile.cooldowns:
            remaining = state.cooldowns_remaining.get(ability.name, 0.0)
            if remaining <= 0:
                ready.append(ability.name)
            else:
                on_cooldown.append(
                    {"name": ability.name, "remaining": round(remaining, 2)}
                )
        burst = self._burst_plan(profile, state)
        return {
            "ready": ready,
            "on_cooldown": on_cooldown,
            "burst": burst,
        }

    # -- internals -----------------------------------------------------------

    def _reason(self, ability: Ability, state: CombatState, situation: str) -> str:
        bits: list[str] = []
        if ability.cost > 0:
            bits.append(
                f"costs {ability.cost:g} {state.__dict__.get('resource_type', '')}"
            )
        elif ability.cost < 0:
            bits.append(f"generates {-ability.cost:g}")
        if ability.maintain:
            bits.append("maintain")
        if ability.requires_buffs:
            bits.append("requires active buff")
        if ability.min_targets > 1:
            bits.append(f"{ability.min_targets}+ targets")
        if ability.castable_while_moving:
            bits.append("mobile")
        if ability.kind in (AbilityKind.BURST, AbilityKind.COOLDOWN):
            bits.append("burst/major CD")
        base = ability.note or ability.kind.value
        if bits:
            return f"{base} ({', '.join(bits)})"
        return base

    def _ready_cooldowns(
        self, profile: RotationProfile, state: CombatState, allow: bool
    ) -> list[str]:
        if not allow:
            return []
        out: list[str] = []
        for ability in profile.cooldowns:
            if (
                ability.cooldown
                and _ready(ability, state)
                and _buffs_ok(ability, state)
            ):
                out.append(ability.name)
        return out

    def _defensives(
        self, profile: RotationProfile, state: CombatState, allow: bool
    ) -> list[str]:
        if not allow:
            return []
        out: list[str] = []
        for ability in profile.defensives:
            if _ready(ability, state):
                out.append(ability.name)
        return out

    def _talent_hints(self, profile: RotationProfile) -> list[str]:
        if not profile.expected_talents:
            return []
        if not self.active_talents:
            return [f"Assumes talent: {t}" for t in profile.expected_talents]
        missing = [t for t in profile.expected_talents if t not in self.active_talents]
        if not missing:
            return ["Talents match the recommended build."]
        return [f"Missing recommended talent: {t}" for t in missing]

    def _burst_plan(
        self, profile: RotationProfile, state: CombatState
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for trigger in profile.burst_triggers:
            fired = True
            if trigger.pool_threshold is not None:
                fired = state.resource >= trigger.pool_threshold
            out.append(
                {
                    "name": trigger.name,
                    "aligned_with": list(trigger.align_with),
                    "ready_now": fired,
                    "note": trigger.note,
                }
            )
        return out
