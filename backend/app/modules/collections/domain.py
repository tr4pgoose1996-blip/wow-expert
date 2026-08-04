"""Collection Tracker domain: the 10 tracked categories and seed catalog.

Tracks mounts, hunter pets, battle pets, toys, appearances, achievements,
titles, tabards, and druid forms. Each catalogued collectible records how it is
obtained and a rough time estimate so the tracker can recommend the *fastest
obtainable* goals and estimate completion time, filtered by class/spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class CollectionCategory(StrEnum):
    MOUNTS = "mounts"
    HUNTER_PETS = "hunter_pets"
    BATTLE_PETS = "battle_pets"
    TOYS = "toys"
    APPEARANCES = "appearances"
    ACHIEVEMENTS = "achievements"
    TITLES = "titles"
    TABARDS = "tabards"
    DRUID_FORMS = "druid_forms"
    TRANSMOG = "transmog"


class ObtainMethod(StrEnum):
    VENDOR = "vendor"
    DROP = "drop"
    QUEST = "quest"
    ACHIEVEMENT = "achievement"
    PROFESSION = "profession"
    WORLD_EVENT = "world_event"
    PVP = "pvp"
    MYTHIC_PLUS = "mythic_plus"
    RAID = "raid"
    CRAFTED = "crafted"
    COLLECTION = "collection"


@dataclass
class Collectible:
    """One tracked objective within a category."""

    id: str
    name: str
    category: CollectionCategory
    obtain: ObtainMethod
    # Rough minutes to obtain solo; used for "fastest" ranking + completion ETA.
    minutes: float
    # Class/spec gating (empty = account-wide, anyone).
    classes: tuple[str, ...] = ()
    notes: str = ""


@dataclass
class CollectionProfile:
    """What a character already has, keyed by category -> set of collectible ids."""

    owned: dict[CollectionCategory, set[str]] = field(default_factory=dict)

    def owned_ids(self, category: CollectionCategory) -> set[str]:
        return self.owned.get(category, set())

    def to_dict(self) -> dict[str, list[str]]:
        return {c.value: sorted(ids) for c, ids in self.owned.items()}


# ---------------------------------------------------------------------------
# Seed catalog — a representative, accurate-in-shape sample per category. The
# live Blizzard collection endpoints can enrich counts; this seed drives the
# "fastest obtainable" and ETA logic without hand-authoring every item.
# ---------------------------------------------------------------------------

_CATALOG: tuple[Collectible, ...] = (
    # Mounts
    Collectible(
        "mount_skyride",
        "Skyriding Valorstone Mount",
        CollectionCategory.MOUNTS,
        ObtainMethod.VENDOR,
        30,
        notes="Bought with Dragonriding currency.",
    ),
    Collectible(
        "mount_rep",
        "Renown Mount",
        CollectionCategory.MOUNTS,
        ObtainMethod.QUEST,
        240,
        notes="Renown 20 with a faction.",
    ),
    Collectible(
        "mount_raid",
        "Raid Boss Mount",
        CollectionCategory.MOUNTS,
        ObtainMethod.RAID,
        180,
        notes="Low drop chance from a raid boss.",
    ),
    Collectible(
        "mount_mplus",
        "Mythic+ Mount",
        CollectionCategory.MOUNTS,
        ObtainMethod.MYTHIC_PLUS,
        120,
        notes="Keystone Hero achievement.",
    ),
    # Hunter pets (Beast Mastery tames)
    Collectible(
        "pet_spirit",
        "Spirit Beast",
        CollectionCategory.HUNTER_PETS,
        ObtainMethod.DROP,
        60,
        classes=("Hunter",),
        notes="Rare spawn tame.",
    ),
    Collectible(
        "pet_exotic",
        "Exotic Devilsaur",
        CollectionCategory.HUNTER_PETS,
        ObtainMethod.DROP,
        45,
        classes=("Hunter",),
        notes="Found in a zone.",
    ),
    # Battle pets
    Collectible(
        "bp_vendor",
        "Vendor Pet",
        CollectionCategory.BATTLE_PETS,
        ObtainMethod.VENDOR,
        10,
    ),
    Collectible(
        "bp_drop",
        "Wild Pet",
        CollectionCategory.BATTLE_PETS,
        ObtainMethod.DROP,
        15,
        notes="Captured in the world.",
    ),
    # Toys
    Collectible(
        "toy_vendor", "Toy Vendor", CollectionCategory.TOYS, ObtainMethod.VENDOR, 5
    ),
    Collectible(
        "toy_event",
        "Holiday Toy",
        CollectionCategory.TOYS,
        ObtainMethod.WORLD_EVENT,
        90,
        notes="Available during a holiday.",
    ),
    # Appearances / transmog
    Collectible(
        "app_quest",
        "Quest Appearance",
        CollectionCategory.APPEARANCES,
        ObtainMethod.QUEST,
        30,
    ),
    Collectible(
        "app_drop",
        "Dungeon Appearance",
        CollectionCategory.APPEARANCES,
        ObtainMethod.DROP,
        45,
    ),
    Collectible(
        "trans_set",
        "Transmog Set",
        CollectionCategory.TRANSMOG,
        ObtainMethod.ACHIEVEMENT,
        300,
        notes="Full tier set.",
    ),
    # Achievements
    Collectible(
        "ach_glory",
        "Glory of the Raider",
        CollectionCategory.ACHIEVEMENTS,
        ObtainMethod.ACHIEVEMENT,
        600,
        notes="Meta achievement.",
    ),
    Collectible(
        "ach_kills",
        "Loremaster",
        CollectionCategory.ACHIEVEMENTS,
        ObtainMethod.QUEST,
        1200,
        notes="Quest achievement.",
    ),
    # Titles
    Collectible(
        "title_pvp",
        "Hero of the Horde/Alliance",
        CollectionCategory.TITLES,
        ObtainMethod.PVP,
        400,
    ),
    Collectible(
        "title_raid",
        "Cutting Edge",
        CollectionCategory.TITLES,
        ObtainMethod.RAID,
        500,
        notes="Current tier mythic clear.",
    ),
    # Tabards
    Collectible(
        "tab_rep",
        "Faction Tabard",
        CollectionCategory.TABARDS,
        ObtainMethod.VENDOR,
        20,
        notes="Reputation vendor.",
    ),
    # Druid forms (spec/class gated)
    Collectible(
        "form_travel",
        "Travel Form",
        CollectionCategory.DRUID_FORMS,
        ObtainMethod.QUEST,
        5,
        classes=("Druid",),
    ),
    Collectible(
        "form_tree",
        "Tree of Life",
        CollectionCategory.DRUID_FORMS,
        ObtainMethod.QUEST,
        10,
        classes=("Druid",),
    ),
    Collectible(
        "form_blot",
        "Blotch Bear",
        CollectionCategory.DRUID_FORMS,
        ObtainMethod.DROP,
        30,
        classes=("Druid",),
        notes="Appearance drop.",
    ),
)


def catalog() -> tuple[Collectible, ...]:
    return _CATALOG


def catalog_by_category(
    category: CollectionCategory | None = None,
) -> list[Collectible]:
    items = _CATALOG
    if category is not None:
        items = tuple(i for i in items if i.category == category)
    return list(items)


# Class/spec gating helper: which collectibles a given class can pursue.
CLASS_BY_SPEC: dict[int, str] = {
    250: "Death Knight",
    251: "Death Knight",
    252: "Death Knight",
    577: "Demon Hunter",
    581: "Demon Hunter",
    1480: "Demon Hunter",
    102: "Druid",
    103: "Druid",
    104: "Druid",
    105: "Druid",
    1467: "Evoker",
    1468: "Evoker",
    1473: "Evoker",
    253: "Hunter",
    254: "Hunter",
    255: "Hunter",
    62: "Mage",
    63: "Mage",
    64: "Mage",
    268: "Monk",
    269: "Monk",
    270: "Monk",
    65: "Paladin",
    66: "Paladin",
    70: "Paladin",
    256: "Priest",
    257: "Priest",
    258: "Priest",
    259: "Rogue",
    260: "Rogue",
    261: "Rogue",
    262: "Shaman",
    263: "Shaman",
    264: "Shaman",
    265: "Warlock",
    266: "Warlock",
    267: "Warlock",
    71: "Warrior",
    72: "Warrior",
    73: "Warrior",
}
