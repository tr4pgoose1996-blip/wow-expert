"""Combat Coach domain: dungeons, raids, bosses, difficulties, affixes.

The coach teaches *how to play* each encounter for every role and difficulty.
Encounter metadata can be hydrated from Blizzard's Journal API; the teaching
logic is generated here from structured facts so it scales to every boss
without hand-authoring prose per combination.

Authoritative Blizzard endpoints (namespace ``static-{region}``):
  GET /data/wow/journal-instance/{id}
  GET /data/wow/journal-encounter/{id}
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ContentType(StrEnum):
    """What kind of content an instance is."""

    DUNGEON = "dungeon"
    RAID = "raid"
    MYTHIC_PLUS = "mythic_plus"  # a dungeon on the seasonal M+ rotation


class Difficulty(StrEnum):
    """Raid/dungeon difficulties, lowest to highest pressure."""

    LFR = "lfr"
    NORMAL = "normal"
    HEROIC = "heroic"
    MYTHIC = "mythic"
    MYTHIC_PLUS = "mythic_plus"

    @property
    def rank(self) -> int:
        return {
            Difficulty.LFR: 0,
            Difficulty.NORMAL: 1,
            Difficulty.HEROIC: 2,
            Difficulty.MYTHIC: 3,
            Difficulty.MYTHIC_PLUS: 4,
        }[self]

    @property
    def is_mythic_plus(self) -> bool:
        return self in (Difficulty.MYTHIC_PLUS,)

    @property
    def is_mythic_tier(self) -> bool:
        return self in (Difficulty.MYTHIC, Difficulty.MYTHIC_PLUS)


class Role(StrEnum):
    TANK = "tank"
    HEALER = "healer"
    DPS = "dps"


class Affix(StrEnum):
    """Seasonal Mythic+ affixes (the scalable ones the coach teaches around)."""

    FORTIFIED = "fortified"
    TYRANNICAL = "tyrannical"
    BURSTING = "bursting"
    RAGING = "raging"
    STORMING = "storming"
    SPITEFUL = "spiteful"
    VOLCANIC = "volcanic"
    GRIEVOUS = "grievous"
    EXPULSIVE = "explosive"
    SANGUINE = "sanguine"
    PRIDEFUL = "prideful"
    INSPIRING = "inspiring"
    SPITEWARD = "spiteward"
    THUNDERING = "thundering"
    BLISTERING = "blistering"
    QUAKE = "quake"
    ENCRYPTED = "encrypted"
    SHROUDED = "shrouded"
    INCINERATING = "incinerating"
    OBSOLETE = "obsolete"


@dataclass
class BossMechanic:
    """One teachable mechanic of a boss.

    ``roles`` restricts who must deal with it; empty means the whole group.
    ``scaling`` marks mechanics that get harder at higher difficulty (so the
    coach can emphasise them on Heroic/Mythic/M+).
    """

    name: str
    description: str
    category: (
        str  # interrupt | movement | defensive | positioning | add | burst | awareness
    )
    roles: tuple[Role, ...] = ()
    scaling: bool = True
    interruptible: bool = False
    # Difficulty at/above which this mechanic appears or intensifies.
    min_difficulty: Difficulty = Difficulty.NORMAL


@dataclass
class Boss:
    """A single boss (or boss-style encounter)."""

    journal_id: int
    name: str
    instance_id: int
    mechanics: tuple[BossMechanic, ...] = ()
    # Short group-level summary of the fight.
    summary: str = ""
    # Things that routinely wipe pugs at this boss.
    common_mistakes: tuple[str, ...] = ()


@dataclass
class Instance:
    """A dungeon or raid."""

    journal_id: int
    name: str
    content_type: ContentType
    bosses: tuple[Boss, ...] = ()
    # For M+ dungeons: the seasonal affixes that matter most here.
    notable_affixes: tuple[Affix, ...] = ()

    def bosses_for(self, difficulty: Difficulty) -> list[Boss]:
        """Bosses that are present at the given difficulty.

        Mythic+ dungeons show all bosses; some raids gate bosses by
        difficulty, but the Journal lists them all, so we return the whole
        set and let the teaching layer scale severity instead.
        """
        return list(self.bosses)


# ---------------------------------------------------------------------------
# Seed content. This is structured reference data for the current season's
# dungeons and a representative raid. IDs are Blizzard journal-instance ids
# where known; the journal client can enrich live. It is intentionally data
# only: the coach generates teaching from it, never special-cases a boss.
# ---------------------------------------------------------------------------

_SEASON_DUNGEONS: tuple[Instance, ...] = (
    Instance(
        journal_id=0,
        name="Example Dungeon A",
        content_type=ContentType.DUNGEON,
        notable_affixes=(Affix.FORTIFIED, Affix.STORMING, Affix.EXPULSIVE),
        bosses=(
            Boss(
                journal_id=1,
                name="First Boss",
                instance_id=0,
                summary="Tank-and-spank with a telegraphed slam and an add wave.",
                mechanics=(
                    BossMechanic(
                        "Crushing Slam",
                        "Telegraphed AoE; dodge or shield.",
                        "defensive",
                        roles=(Role.TANK,),
                        scaling=True,
                    ),
                    BossMechanic(
                        "Summon Adds",
                        "Adds must be picked up and cleaved.",
                        "add",
                        roles=(Role.TANK, Role.DPS),
                        scaling=True,
                    ),
                    BossMechanic(
                        "Quake",
                        "Raid-wide pulse; heal through it.",
                        "awareness",
                        roles=(Role.HEALER,),
                        scaling=False,
                    ),
                ),
                common_mistakes=(
                    "Tank faces slam into the group.",
                    "DPS ignore adds and they kill the healer.",
                ),
            ),
            Boss(
                journal_id=2,
                name="Second Boss",
                instance_id=0,
                summary="Movement check with interrupts and a burst window.",
                mechanics=(
                    BossMechanic(
                        "Cast to Interrupt",
                        "Hard-cast that must be kicked.",
                        "interrupt",
                        roles=(Role.DPS,),
                        scaling=False,
                        interruptible=True,
                    ),
                    BossMechanic(
                        "Spread Out",
                        "Avoid stacking during the beam phase.",
                        "movement",
                        scaling=True,
                    ),
                    BossMechanic(
                        "Vulnerable",
                        "Boss takes +50% damage; pop cooldowns.",
                        "burst",
                        roles=(Role.DPS,),
                        scaling=False,
                    ),
                ),
                common_mistakes=(
                    "Nobody kicks the cast.",
                    "Cooldowns wasted outside the vulnerable window.",
                ),
            ),
        ),
    ),
    Instance(
        journal_id=1,
        name="Example Dungeon B",
        content_type=ContentType.DUNGEON,
        notable_affixes=(Affix.TYRANNICAL, Affix.VOLCANIC, Affix.SANGUINE),
        bosses=(
            Boss(
                journal_id=3,
                name="Trash Gauntlet",
                instance_id=1,
                summary="Long trash pull with dangerous casters.",
                mechanics=(
                    BossMechanic(
                        "Caster Pull",
                        "Interrupt the healer-caster first.",
                        "interrupt",
                        roles=(Role.DPS,),
                        interruptible=True,
                        scaling=False,
                    ),
                    BossMechanic(
                        "Patrol Timing",
                        "Wait for the patrol to pass.",
                        "movement",
                        scaling=True,
                    ),
                ),
                common_mistakes=("Pulling the patrol with the pack."),
            ),
        ),
    ),
)

_SEASON_RAID: Instance = Instance(
    journal_id=2,
    name="Example Raid",
    content_type=ContentType.RAID,
    bosses=(
        Boss(
            journal_id=10,
            name="Raid Boss One",
            instance_id=2,
            summary="Two-phase fight; phase two adds heavy raid damage.",
            mechanics=(
                BossMechanic(
                    "Phase Transition",
                    "At 60% the boss enrages; burn cooldowns.",
                    "burst",
                    roles=(Role.DPS,),
                    scaling=True,
                ),
                BossMechanic(
                    "Soak Orb",
                    "One player soaks each orb to avoid raid damage.",
                    "positioning",
                    roles=(Role.DPS, Role.HEALER),
                    scaling=True,
                ),
                BossMechanic(
                    "Tank Swap",
                    "Swap at 3 stacks of the debuff.",
                    "defensive",
                    roles=(Role.TANK,),
                    scaling=True,
                ),
            ),
            common_mistakes=(
                "Tanks don't swap and die to the debuff.",
                "Healers under-prepare for phase two.",
            ),
        ),
    ),
)

INSTANCES: tuple[Instance, ...] = (*_SEASON_DUNGEONS, _SEASON_RAID)
INSTANCE_BY_ID: dict[int, Instance] = {i.journal_id: i for i in INSTANCES}


def list_instances(content_type: ContentType | None = None) -> list[Instance]:
    if content_type is None:
        return list(INSTANCES)
    return [i for i in INSTANCES if i.content_type == content_type]
