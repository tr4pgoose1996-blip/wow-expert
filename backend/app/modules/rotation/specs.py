"""Specialization registry and rotation-domain types.

Every playable specialization in the current game is catalogued here with its
Blizzard specialization id, class, and role. The ids come from
``GetSpecializationInfoByID`` (ChrSpecialization.db2); they are authoritative
and include the current-expansion Demon Hunter third spec, Devourer (1480).

Authoring a rotation profile for a spec means adding one entry to
:data:`ROTATION_PROFILES` — nothing else in the engine changes, because the
advisor works generically over the :class:`RotationProfile` shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ResourceType(StrEnum):
    """Primary resource a specialization spends to act.

    Several specializations also have a secondary resource (e.g. combo
    points are a *generator* state, not a pool); those live on the profile as
    ``secondary_resource`` rather than here, because the priority logic treats
    them differently.
    """

    RAGE = "rage"
    ENERGY = "energy"
    FOCUS = "focus"
    MANA = "mana"
    RUNIC_POWER = "runic_power"
    SOUL_SHARDS = "soul_shards"
    HOLY_POWER = "holy_power"
    FURY = "fury"
    MAELSTROM = "maelstrom"
    INSANITY = "insanity"
    NONE = "none"


class AbilityKind(StrEnum):
    """What an ability is for, used by the priority and cooldown planners."""

    CORE = "core"  # bread-and-butter rotational filler/builder
    BUILDER = "builder"  # generates the primary resource
    SPENDER = "spender"  # consumes the primary resource
    COOLDOWN = "cooldown"  # major offensive/defensive on a long timer
    BURST = "burst"  # aligned with burst windows (trinkets, etc.)
    DEFENSIVE = "defensive"  # mitigation, not damage
    UTILITY = "utility"  # interrupt, mobility, purge, etc.
    DOT = "dot"  # damage-over-time, maintained
    PROC = "proc"  # used when available (no cooldown math)


@dataclass(frozen=True)
class Ability:
    """A single rotational ability.

    ``id`` is the spell id where known; it is optional and only used for
    gear/trinket cross-referencing. ``priority`` is the tie-break within a
    phase: lower runs first. ``weight`` biases the priority system when two
    abilities are both eligible (higher wins).
    """

    name: str
    kind: AbilityKind
    spell_id: int | None = None
    priority: int = 100
    weight: float = 1.0
    # Pool/cost in resource points. For builders this is negative (refund).
    cost: float = 0.0
    # Seconds; 0 means no cooldown (proc/dot refresh handled by maintain flag).
    cooldown: float = 0.0
    # Required number of targets for AoE-only abilities (0 = single-target ok).
    min_targets: int = 0
    # Defensive abilities declare mitigation so the coach can plan them.
    mitigation: float = 0.0
    # Spell ids that must be active for this to be worth casting (talents/dots).
    requires_buffs: tuple[int, ...] = ()
    # Spell ids that, if active, make this *not* worth casting yet.
    avoid_if_buffs: tuple[int, ...] = ()
    # Only used when the linked talent node is picked.
    requires_talent: str | None = None
    # Marks a maintained damage-over-time effect.
    maintain: bool = False
    # Used by movement adaptation: castable while moving.
    castable_while_moving: bool = False
    # Brief human note surfaced by the advisor.
    note: str = ""


@dataclass(frozen=True)
class BurstTrigger:
    """Condition that opens a burst window."""

    name: str
    # Pool threshold (>=) to pop when ready, e.g. enough resources banked.
    pool_threshold: float | None = None
    # Alignment note, e.g. "with trinket + racial".
    align_with: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class RotationProfile:
    """Complete, machine-readable rotation for one specialization.

    This is the contract the advisor consumes. It is intentionally data-only
    so profiles can be authored, validated, and seeded without touching code.
    """

    spec_id: int
    spec_name: str
    wow_class: str
    role: str
    resource: ResourceType
    secondary_resource: str | None = None

    opener: tuple[Ability, ...] = ()
    # Single-target priority list (ordered, highest priority first).
    single_target: tuple[Ability, ...] = ()
    # AoE priority list; falls back to single_target when under min_targets.
    aoe: tuple[Ability, ...] = ()
    # Abilities used while moving (subset; default = castable_while_moving).
    movement: tuple[Ability, ...] = ()
    # Defensive abilities the coach may plan.
    defensives: tuple[Ability, ...] = ()
    # Major cooldowns and how to sequence them.
    cooldowns: tuple[Ability, ...] = ()
    burst_triggers: tuple[BurstTrigger, ...] = ()

    # Execute range: below this target health %, execute logic applies.
    execute_below_pct: float | None = None
    # Talents the profile assumes; used for talent awareness hints.
    expected_talents: tuple[str, ...] = ()
    # Gear/trinket notes surfaced by optimization.
    trinket_guidance: str = ""
    gear_guidance: str = ""
    notes: str = ""


@dataclass
class CombatState:
    """Mutable snapshot of the fight the advisor reasons over.

    Callers (or the live combat log parser, later) populate this each tick.
    """

    spec_id: int
    target_health_pct: float = 100.0
    targets: int = 1
    # Current primary resource pool value.
    resource: float = 0.0
    resource_max: float = 100.0
    moving: bool = False
    # Seconds since pull.
    time: float = 0.0
    # Remaining cooldowns keyed by ability name -> seconds left.
    cooldowns_remaining: dict[str, float] = field(default_factory=dict)
    # Active buff spell ids.
    active_buffs: set[int] = field(default_factory=set)
    # Whether a burst window is currently open (e.g. potion used).
    burst_active: bool = False
    # Out of mana / starved flag.
    starved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "target_health_pct": self.target_health_pct,
            "targets": self.targets,
            "resource": self.resource,
            "resource_max": self.resource_max,
            "moving": self.moving,
            "time": self.time,
            "cooldowns_remaining": dict(self.cooldowns_remaining),
            "active_buffs": sorted(self.active_buffs),
            "burst_active": self.burst_active,
            "starved": self.starved,
        }


# ---------------------------------------------------------------------------
# Specialization catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Specialization:
    spec_id: int
    name: str
    wow_class: str
    role: str


# Authoritative ids from ChrSpecialization.db2. The "Initial" specs (14xx) are
# not playable and are excluded. Demon Hunter's third spec Devourer (1480) is
# current and real.
SPECIALIZATIONS: tuple[Specialization, ...] = (
    # Death Knight
    Specialization(250, "Blood", "Death Knight", "tank"),
    Specialization(251, "Frost", "Death Knight", "damage"),
    Specialization(252, "Unholy", "Death Knight", "damage"),
    # Demon Hunter
    Specialization(577, "Havoc", "Demon Hunter", "damage"),
    Specialization(581, "Vengeance", "Demon Hunter", "tank"),
    Specialization(1480, "Devourer", "Demon Hunter", "damage"),
    # Druid
    Specialization(102, "Balance", "Druid", "damage"),
    Specialization(103, "Feral", "Druid", "damage"),
    Specialization(104, "Guardian", "Druid", "tank"),
    Specialization(105, "Restoration", "Druid", "healer"),
    # Evoker
    Specialization(1467, "Devastation", "Evoker", "damage"),
    Specialization(1468, "Preservation", "Evoker", "healer"),
    Specialization(1473, "Augmentation", "Evoker", "damage"),
    # Hunter
    Specialization(253, "Beast Mastery", "Hunter", "damage"),
    Specialization(254, "Marksmanship", "Hunter", "damage"),
    Specialization(255, "Survival", "Hunter", "damage"),
    # Mage
    Specialization(62, "Arcane", "Mage", "damage"),
    Specialization(63, "Fire", "Mage", "damage"),
    Specialization(64, "Frost", "Mage", "damage"),
    # Monk
    Specialization(268, "Brewmaster", "Monk", "tank"),
    Specialization(269, "Windwalker", "Monk", "damage"),
    Specialization(270, "Mistweaver", "Monk", "healer"),
    # Paladin
    Specialization(65, "Holy", "Paladin", "healer"),
    Specialization(66, "Protection", "Paladin", "tank"),
    Specialization(70, "Retribution", "Paladin", "damage"),
    # Priest
    Specialization(256, "Discipline", "Priest", "healer"),
    Specialization(257, "Holy", "Priest", "healer"),
    Specialization(258, "Shadow", "Priest", "damage"),
    # Rogue
    Specialization(259, "Assassination", "Rogue", "damage"),
    Specialization(260, "Outlaw", "Rogue", "damage"),
    Specialization(261, "Subtlety", "Rogue", "damage"),
    # Shaman
    Specialization(262, "Elemental", "Shaman", "damage"),
    Specialization(263, "Enhancement", "Shaman", "damage"),
    Specialization(264, "Restoration", "Shaman", "healer"),
    # Warlock
    Specialization(265, "Affliction", "Warlock", "damage"),
    Specialization(266, "Demonology", "Warlock", "damage"),
    Specialization(267, "Destruction", "Warlock", "damage"),
    # Warrior
    Specialization(71, "Arms", "Warrior", "damage"),
    Specialization(72, "Fury", "Warrior", "damage"),
    Specialization(73, "Protection", "Warrior", "tank"),
)

SPEC_BY_ID: dict[int, Specialization] = {s.spec_id: s for s in SPECIALIZATIONS}
SPEC_COUNT = len(SPECIALIZATIONS)
