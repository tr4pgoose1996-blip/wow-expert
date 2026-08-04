"""Combat Coach teaching generator.

Turns structured encounter facts (from :mod:`app.modules.coach.domain`) plus a
chosen role, difficulty, and (for Mythic+) affix set into role-specific,
difficulty-scaled coaching. The generator is pure: no I/O, fully unit-testable,
and never special-cases a single boss — every combination of boss/role/difficulty
is produced by the same rules so coverage scales to every dungeon, raid, boss,
and Mythic+ keystone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.coach.domain import (
    Affix,
    Boss,
    BossMechanic,
    Difficulty,
    Instance,
    Role,
)

# Difficulty multipliers applied to "how hard" a scaling mechanic is. Used to
# decide whether to surface a mechanic prominently and how strongly to word it.
_DIFFICULTY_WEIGHT: dict[Difficulty, float] = {
    Difficulty.LFR: 0.4,
    Difficulty.NORMAL: 0.7,
    Difficulty.HEROIC: 1.0,
    Difficulty.MYTHIC: 1.3,
    Difficulty.MYTHIC_PLUS: 1.5,
}

# Affixes primarily punish specific roles; the coach leans into those.
_AFFIX_FOCUS: dict[Affix, tuple[Role, ...]] = {
    Affix.FORTIFIED: (Role.TANK, Role.HEALER),
    Affix.TYRANNICAL: (Role.DPS, Role.HEALER),
    Affix.BURSTING: (Role.HEALER, Role.TANK),
    Affix.RAGING: (Role.TANK, Role.DPS),
    Affix.STORMING: (Role.DPS, Role.HEALER),
    Affix.SPITEFUL: (Role.DPS,),
    Affix.VOLCANIC: (Role.DPS, Role.HEALER),
    Affix.GRIEVOUS: (Role.HEALER,),
    Affix.EXPULSIVE: (Role.DPS,),
    Affix.SANGUINE: (Role.DPS, Role.TANK),
    Affix.PRIDEFUL: (Role.DPS,),
    Affix.INSPIRING: (Role.DPS, Role.TANK),
    Affix.THUNDERING: (Role.DPS, Role.HEALER),
    Affix.BLISTERING: (Role.HEALER, Role.DPS),
    Affix.QUAKE: (Role.DPS, Role.HEALER),
    Affix.ENCRYPTED: (Role.DPS,),
    Affix.SHROUDED: (Role.DPS,),
    Affix.INCINERATING: (Role.HEALER,),
    Affix.OBSOLETE: (),
    Affix.SPITEWARD: (Role.TANK, Role.HEALER),
}

_AFFIX_TEACHING: dict[Affix, str] = {
    Affix.FORTIFIED: "Fortified: trash hits harder. Pull in packs; stagger interrupts.",
    Affix.TYRANNICAL: "Tyrannical: bosses hit harder. Save defensives for spikes.",
    Affix.BURSTING: "Bursting: deaths stack a party DoT. Healers pre-hot; no mass AoE.",
    Affix.RAGING: "Raging: mobs enrage at 30%. Stun or kite during the enrage.",
    Affix.STORMING: "Storming: tornadoes chase you. Keep moving; dodge the swirls.",
    Affix.SPITEFUL: "Spiteful: shades fixate healers. DPS peel them off at once.",
    Affix.VOLCANIC: "Volcanic: eruptions spawn under you. Don't stand in the fire.",
    Affix.GRIEVOUS: "Grievous: damage-over-time at low health. Healers top up fast.",
    Affix.EXPULSIVE: "Explosive: orbs detonate. Cleave them down before they pop.",
    Affix.SANGUINE: "Sanguine: death pools heal mobs. Pull away so they don't heal.",
    Affix.PRIDEFUL: "Prideful: kill the manifest for Pride and a big damage buff.",
    Affix.INSPIRING: "Inspiring: a flag bearer shields mobs. Interrupt to drop it.",
    Affix.THUNDERING: "Thundering: chains mark you; soak with an ally or take damage.",
    Affix.BLISTERING: "Blistering: heat builds; vent by stepping in fire or you burst.",
    Affix.QUAKE: "Quake: at 20% the caster overloads; stop casting or get locked.",
    Affix.ENCRYPTED: "Encrypted: discharge obelisks to spawn a reward chest.",
    Affix.SHROUDED: "Shrouded: stealth past or kill the marked target for loot.",
    Affix.INCINERATING: "Incinerating: avoid the beam; it applies a stacking burn.",
    Affix.OBSOLETE: "Obsolete affix (legacy).",
    Affix.SPITEWARD: "Spiteward: shields reflect; time offensive cooldowns around it.",
}


@dataclass
class CoachPoint:
    """One coaching instruction for the requested role/difficulty."""

    category: str
    text: str
    severity: str  # info | important | critical


@dataclass
class CoachBriefing:
    """A full role-specific briefing for one boss at one difficulty."""

    instance_name: str
    boss_name: str
    role: Role
    difficulty: Difficulty
    affixes: tuple[Affix, ...] = ()
    summary: str = ""
    briefing: list[CoachPoint] = field(default_factory=list)
    common_mistakes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_name": self.instance_name,
            "boss_name": self.boss_name,
            "role": self.role.value,
            "difficulty": self.difficulty.value,
            "affixes": [a.value for a in self.affixes],
            "summary": self.summary,
            "briefing": [
                {"category": p.category, "text": p.text, "severity": p.severity}
                for p in self.briefing
            ],
            "common_mistakes": self.common_mistakes,
        }


def _mechanic_visible(mechanic: BossMechanic, difficulty: Difficulty) -> bool:
    """A mechanic is taught only if the difficulty meets its gate."""
    return difficulty.rank >= mechanic.min_difficulty.rank


def _severity_for(mechanic: BossMechanic, difficulty: Difficulty) -> str:
    """Scale wording severity with difficulty and mechanic scaling flag."""
    if not mechanic.scaling:
        return "info"
    weight = _DIFFICULTY_WEIGHT[difficulty]
    if weight >= 1.3:
        return "critical"
    if weight >= 1.0:
        return "important"
    return "info"


def _role_match(mechanic: BossMechanic, role: Role) -> bool:
    """True when the mechanic applies to the requested role."""
    if not mechanic.roles:
        return True  # group-wide mechanic
    return role in mechanic.roles


def _teach_mechanic(mechanic: BossMechanic, difficulty: Difficulty) -> CoachPoint:
    prefix = {
        "interrupt": "Interrupt",
        "movement": "Move",
        "defensive": "Defend",
        "positioning": "Position",
        "add": "Handle adds",
        "burst": "Burst",
        "awareness": "Be aware",
    }.get(mechanic.category, "Note")
    text = f"{prefix}: {mechanic.description}"
    if difficulty.is_mythic_tier and mechanic.scaling:
        text += " (intensifies at this difficulty)"
    return CoachPoint(
        category=mechanic.category,
        text=text,
        severity=_severity_for(mechanic, difficulty),
    )


def generate_briefing(
    instance: Instance,
    boss: Boss,
    role: Role,
    difficulty: Difficulty,
    affixes: tuple[Affix, ...] = (),
) -> CoachBriefing:
    """Build a role/difficulty-specific briefing for one boss.

    The same rules apply to every boss, so the coach covers every dungeon,
    raid, boss, and Mythic+ keystone without per-fight hand-authoring.
    """
    briefing: list[CoachPoint] = []

    # 1. Role framing line.
    role_intro = {
        Role.TANK: "As the tank, you anchor positioning and own mitigations.",
        Role.HEALER: "As the healer, you sustain the group through damage spikes.",
        Role.DPS: "As DPS, you deliver damage while handling your assigned mechanics.",
    }[role]
    briefing.append(CoachPoint("role", role_intro, "info"))

    # 2. Difficulty framing.
    diff_line = {
        Difficulty.LFR: "LFR: learn the dance; mistakes are forgiven.",
        Difficulty.NORMAL: "Normal: clean execution gets you kill-ready.",
        Difficulty.HEROIC: (
            "Heroic: tighter timers and extra consequences — plan cooldowns."
        ),
        Difficulty.MYTHIC: (
            "Mythic: maximum mechanical pressure; every cooldown is accounted for."
        ),
        Difficulty.MYTHIC_PLUS: (
            "Mythic+: affixes and timers dominate; route and plan cooldowns."
        ),
    }[difficulty]
    briefing.append(CoachPoint("difficulty", diff_line, "info"))

    # 3. Group mechanics apply to everyone.
    for m in boss.mechanics:
        if _mechanic_visible(m, difficulty) and not m.roles:
            briefing.append(_teach_mechanic(m, difficulty))

    # 4. Role-specific mechanics.
    for m in boss.mechanics:
        if _mechanic_visible(m, difficulty) and _role_match(m, role):
            briefing.append(_teach_mechanic(m, difficulty))

    # 5. Role-specific cross-cutting coaching.
    briefing.extend(_role_general_coaching(role, difficulty, affixes, instance))

    # 6. Affix coaching (Mythic+).
    if difficulty.is_mythic_plus:
        for affix in affixes:
            briefing.append(
                CoachPoint(
                    "affix",
                    _AFFIX_TEACHING.get(affix, f"Affix: {affix.value}."),
                    "important" if role in _AFFIX_FOCUS.get(affix, ()) else "info",
                )
            )

    # 7. Cooldown timing + burst window note.
    burst_mechanics = [m for m in boss.mechanics if m.category == "burst"]
    if burst_mechanics:
        briefing.append(
            CoachPoint(
                "cooldown_timing",
                "Line offensive and defensive cooldowns to the burst window(s): "
                + "; ".join(m.name for m in burst_mechanics)
                + ".",
                "important",
            )
        )

    return CoachBriefing(
        instance_name=instance.name,
        boss_name=boss.name,
        role=role,
        difficulty=difficulty,
        affixes=tuple(affixes),
        summary=boss.summary,
        briefing=briefing,
        common_mistakes=list(boss.common_mistakes),
    )


def _role_general_coaching(
    role: Role,
    difficulty: Difficulty,
    affixes: tuple[Affix, ...],
    instance: Instance,
) -> list[CoachPoint]:
    points: list[CoachPoint] = []
    sev = _severity_for(
        BossMechanic(
            name="x",
            description="",
            category="awareness",
            scaling=True,
            min_difficulty=difficulty,
        ),
        difficulty,
    )

    if role == Role.TANK:
        points.append(
            CoachPoint(
                "defensive_planning",
                "Plan mitigations before the pull; stagger major defensives across "
                "the tank team rather than stacking them.",
                sev,
            )
        )
        points.append(
            CoachPoint(
                "positioning",
                "Face bosses away from the group and keep dangerous abilities pointed "
                "into a safe wall.",
                "info",
            )
        )
    elif role == Role.HEALER:
        points.append(
            CoachPoint(
                "defensive_planning",
                "Pre-cast HoTs before damage spikes and hold a big heal for the "
                "telegraphed burst.",
                sev,
            )
        )
        points.append(
            CoachPoint(
                "awareness",
                "Watch the tank's mitigation timers and the group's position; you "
                "cannot heal through bad positioning.",
                "info",
            )
        )
    else:  # DPS
        points.append(
            CoachPoint(
                "burst_windows",
                "Pool resources for the boss's vulnerable/burst windows and keep your "
                "damage uptime by moving efficiently.",
                sev,
            )
        )
        points.append(
            CoachPoint(
                "interrupts",
                "Share interrupt assignments on the kill priority; never let a "
                "critical cast go unkicked.",
                "important" if any(a == Affix.INSPIRING for a in affixes) else "info",
            )
        )

    # Movement note scales with difficulty.
    if difficulty.rank >= Difficulty.HEROIC.rank:
        points.append(
            CoachPoint(
                "movement",
                "Higher difficulties punish standing still — pre-position and use "
                "mobility tools on cooldown.",
                sev,
            )
        )

    # Instance-affix emphasis for M+ dungeons.
    if instance.content_type.value == "dungeon" and instance.notable_affixes:
        notable = [a for a in affixes if a in instance.notable_affixes]
        if notable:
            points.append(
                CoachPoint(
                    "affix",
                    "This dungeon is especially punishing with: "
                    + ", ".join(a.value for a in notable)
                    + ". Route around those pulls.",
                    "important",
                )
            )
    return points
