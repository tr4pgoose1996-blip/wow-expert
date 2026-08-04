"""Gear Advisor domain: stats, itemisation, and recommendation seed data.

The advisor reasons about gear generically so it scales to every class and
specialization. Stat weights per spec drive upgrade scoring; enchant, gem,
trinket, and crafted-gear tables are keyed by item slot and role so a single
code path serves all 40 specializations.

Live item stats come from Blizzard's equipment/ item-media endpoints; the seed
data here supplies the *rules* (weights, best enchant per slot, optimal gem
color, notable trinkets, craftable slots) that turn raw stats into advice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Stat(StrEnum):
    """Secondary (and a few primary) stats the advisor scores."""

    INTELLECT = "intellect"
    AGILITY = "agility"
    STRENGTH = "strength"
    STAMINA = "stamina"
    CRIT = "crit"
    HASTE = "haste"
    MASTERY = "mastery"
    VERSATILITY = "versatility"
    LEECH = "leech"
    SPEED = "speed"
    AVOIDANCE = "avoidance"


class ItemSlot(StrEnum):
    HEAD = "head"
    NECK = "neck"
    SHOULDERS = "shoulders"
    BACK = "back"
    CHEST = "chest"
    WRIST = "wrist"
    HANDS = "hands"
    WAIST = "waist"
    LEGS = "legs"
    FEET = "feet"
    FINGER_1 = "finger_1"
    FINGER_2 = "finger_2"
    TRINKET_1 = "trinket_1"
    TRINKET_2 = "trinket_2"
    MAIN_HAND = "main_hand"
    OFF_HAND = "off_hand"


class GearRarity(StrEnum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"
    MYTHIC = "mythic"


@dataclass
class ItemStats:
    """A bundle of stats on an item, expressed as stat_id -> value."""

    values: dict[Stat, float] = field(default_factory=dict)

    def score(self, weights: dict[Stat, float]) -> float:
        """Weighted value of this stat bundle for a given spec."""
        total = 0.0
        for stat, amount in self.values.items():
            total += amount * weights.get(stat, 0.0)
        return total

    @property
    def primary(self) -> Stat | None:
        for s in (Stat.INTELLECT, Stat.AGILITY, Stat.STRENGTH):
            if self.values.get(s):
                return s
        return None

    def to_dict(self) -> dict[str, Any]:
        return {s.value: v for s, v in self.values.items()}


@dataclass
class GearItem:
    """A comparable piece of gear (from Blizzard equipment or user input)."""

    slot: ItemSlot
    name: str
    item_id: int = 0
    item_level: int = 0
    rarity: GearRarity = GearRarity.EPIC
    stats: ItemStats = field(default_factory=ItemStats)
    is_crafted: bool = False
    is_trinket: bool = False
    source: str = ""  # dungeon | raid | vault | crafted | world


@dataclass
class StatWeights:
    """Per-spec stat priority used to score gear."""

    spec_id: int
    weights: dict[Stat, float]


@dataclass
class EnchantRule:
    slot: ItemSlot
    name: str
    stat: Stat
    value: float
    note: str = ""


@dataclass
class GemRule:
    color: str
    stat: Stat
    value: float
    note: str = ""


# ---------------------------------------------------------------------------
# Stat-weight seed: one representative priority per role archetype. Real tuning
# per spec would refine these, but they are correct in shape (a fire mage wants
# crit>haste>mastery, a tank wants versatility/stamina, etc.) and drive the
# scoring engine identically for all 40 specs.
# ---------------------------------------------------------------------------

# role -> base weights; specs inherit then may override.
_ROLE_WEIGHTS: dict[str, dict[Stat, float]] = {
    "damage": {
        Stat.CRIT: 1.0,
        Stat.HASTE: 0.9,
        Stat.MASTERY: 0.8,
        Stat.VERSATILITY: 0.7,
        Stat.INTELLECT: 1.0,
        Stat.AGILITY: 1.0,
        Stat.STRENGTH: 1.0,
        Stat.STAMINA: 0.1,
        Stat.LEECH: 0.2,
        Stat.SPEED: 0.15,
    },
    "tank": {
        Stat.STAMINA: 1.0,
        Stat.VERSATILITY: 0.9,
        Stat.MASTERY: 0.7,
        Stat.HASTE: 0.6,
        Stat.CRIT: 0.4,
        Stat.AVOIDANCE: 0.5,
        Stat.LEECH: 0.5,
        Stat.SPEED: 0.3,
        Stat.INTELLECT: 0.1,
        Stat.AGILITY: 0.1,
        Stat.STRENGTH: 0.1,
    },
    "healer": {
        Stat.INTELLECT: 1.0,
        Stat.CRIT: 0.9,
        Stat.HASTE: 1.0,
        Stat.MASTERY: 0.8,
        Stat.VERSATILITY: 0.7,
        Stat.STAMINA: 0.2,
        Stat.LEECH: 0.4,
        Stat.SPEED: 0.2,
    },
}

# Spec-specific overrides (lower = higher priority when value is larger). These
# capture the well-known differences (e.g. Balance wants mastery, Affliction
# wants haste) without enumerating every nuance.
_SPEC_WEIGHT_OVERRIDES: dict[int, dict[Stat, float]] = {
    102: {Stat.MASTERY: 1.1, Stat.HASTE: 0.95},  # Balance
    104: {Stat.VERSATILITY: 1.0, Stat.MASTERY: 0.8},  # Guardian
    251: {Stat.STRENGTH: 1.0, Stat.CRIT: 1.05, Stat.MASTERY: 0.85},  # Frost DK
    252: {Stat.CRIT: 1.1, Stat.MASTERY: 0.9},  # Unholy
    265: {Stat.HASTE: 1.1, Stat.MASTERY: 0.9},  # Affliction
    266: {Stat.HASTE: 1.1, Stat.MASTERY: 1.0},  # Demonology
    577: {Stat.CRIT: 1.1, Stat.HASTE: 1.0},  # Havoc
    253: {Stat.HASTE: 1.05, Stat.MASTERY: 0.95},  # BM Hunter
}


def weights_for_spec(spec_id: int, role: str) -> dict[Stat, float]:
    base = dict(_ROLE_WEIGHTS.get(role, _ROLE_WEIGHTS["damage"]))
    base.update(_SPEC_WEIGHT_OVERRIDES.get(spec_id, {}))
    return base


# ---------------------------------------------------------------------------
# Enchant / gem / trinket / crafted seed tables.
# ---------------------------------------------------------------------------

ENCHANT_RULES: tuple[EnchantRule, ...] = (
    EnchantRule(
        ItemSlot.BACK, "Breath of the Open Sky", Stat.HASTE, 200, "Best for most DPS."
    ),
    EnchantRule(ItemSlot.CHEST, "Resilient", Stat.STAMINA, 750, "Survivability."),
    EnchantRule(
        ItemSlot.WRIST, "Devotion of Speed", Stat.SPEED, 200, "Utility for DPS."
    ),
    EnchantRule(
        ItemSlot.LEGS, "Frosted Armor Kit", Stat.STAMINA, 900, "Stamina for tanks."
    ),
    EnchantRule(
        ItemSlot.FEET,
        "Plainsrunner's Breeze",
        Stat.SPEED,
        200,
        "Move speed for everyone.",
    ),
    EnchantRule(
        ItemSlot.FINGER_1,
        "Treatise on Resolve",
        Stat.VERSATILITY,
        190,
        "Versatility ring enchant.",
    ),
    EnchantRule(
        ItemSlot.MAIN_HAND,
        "Sophic Devotion",
        Stat.INTELLECT,
        350,
        "Intellect weapon enchant.",
    ),
)

GEM_RULES: tuple[GemRule, ...] = (
    GemRule("red", Stat.CRIT, 150, "Crit gem."),
    GemRule("blue", Stat.MASTERY, 150, "Mastery gem."),
    GemRule("yellow", Stat.HASTE, 150, "Haste gem."),
    GemRule("orange", Stat.CRIT, 150, "Crit/haste socket bonus."),
)

# Notable trinkets per role, scored by a rough desirability 0-100.
TRINKET_TIERS: dict[str, list[tuple[str, int, str]]] = {
    "damage": [
        ("Aqua Raptorial", 95, "On-use crit proc; top DPS trinket."),
        ("Voidmender's Chisel", 88, "High mastery proc."),
        ("Eye of Kezan", 82, "Versatile on-use."),
    ],
    "tank": [
        ("Bulwark of the Steadfast", 92, "Active mitigation on-use."),
        ("Ara-Kara Signet", 80, "Stamina + vers proc."),
    ],
    "healer": [
        ("Nexus Pavestone", 90, "Intellect + crit proc."),
        ("Sikran's Compass", 84, "Throughput on-use."),
    ],
}

# Slots that can be crafted with a missive (so players can target a stat).
CRAFTABLE_SLOTS: tuple[ItemSlot, ...] = (
    ItemSlot.HEAD,
    ItemSlot.CHEST,
    ItemSlot.WRIST,
    ItemSlot.HANDS,
    ItemSlot.WAIST,
    ItemSlot.LEGS,
    ItemSlot.FEET,
    ItemSlot.FINGER_1,
    ItemSlot.FINGER_2,
    ItemSlot.NECK,
    ItemSlot.BACK,
)

# Slots where a vault choice matters most (high-stat, often best-in-slot).
VAULT_PRIORITY_SLOTS: tuple[ItemSlot, ...] = (
    ItemSlot.TRINKET_1,
    ItemSlot.TRINKET_2,
    ItemSlot.MAIN_HAND,
    ItemSlot.CHEST,
    ItemSlot.LEGS,
    ItemSlot.HEAD,
)
