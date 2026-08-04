"""Shared WoW domain enumerations."""

from __future__ import annotations

from enum import StrEnum


class Faction(StrEnum):
    ALLIANCE = "alliance"
    HORDE = "horde"
    NEUTRAL = "neutral"


class CharacterClass(StrEnum):
    DEATH_KNIGHT = "death_knight"
    DEMON_HUNTER = "demon_hunter"
    DRUID = "druid"
    EVOKER = "evoker"
    HUNTER = "hunter"
    MAGE = "mage"
    MONK = "monk"
    PALADIN = "paladin"
    PRIEST = "priest"
    ROGUE = "rogue"
    SHAMAN = "shaman"
    WARLOCK = "warlock"
    WARRIOR = "warrior"


class Role(StrEnum):
    TANK = "tank"
    HEALER = "healer"
    DAMAGE = "damage"


class ContentFocus(StrEnum):
    """What a player is primarily working on — drives coaching advice."""

    LEVELING = "leveling"
    MYTHIC_PLUS = "mythic_plus"
    RAIDING = "raiding"
    PVP = "pvp"
    PROFESSIONS = "professions"
    COLLECTING = "collecting"
    ROLEPLAY = "roleplay"


class UserRole(StrEnum):
    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"


class SyncScope(StrEnum):
    """A unit of importable Blizzard data."""

    PROFILE = "profile"
    EQUIPMENT = "equipment"
    TALENTS = "talents"
    PROFESSIONS = "professions"
    ACHIEVEMENTS = "achievements"
    REPUTATIONS = "reputations"
    MOUNTS = "mounts"
    PETS = "pets"
    ACCOUNT_ROSTER = "account_roster"
    FULL = "full"


#: Scopes covered by a "full" character synchronization.
CHARACTER_SYNC_SCOPES: tuple[SyncScope, ...] = (
    SyncScope.PROFILE,
    SyncScope.EQUIPMENT,
    SyncScope.TALENTS,
    SyncScope.PROFESSIONS,
    SyncScope.ACHIEVEMENTS,
    SyncScope.REPUTATIONS,
    SyncScope.MOUNTS,
    SyncScope.PETS,
)


class SyncStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


# Which specialisation roles each class can legally fill. Used to validate
# character profiles so coaching advice is never generated for an
# impossible combination (e.g. a mage tank).
CLASS_ROLES: dict[CharacterClass, frozenset[Role]] = {
    CharacterClass.DEATH_KNIGHT: frozenset({Role.TANK, Role.DAMAGE}),
    CharacterClass.DEMON_HUNTER: frozenset({Role.TANK, Role.DAMAGE}),
    CharacterClass.DRUID: frozenset({Role.TANK, Role.HEALER, Role.DAMAGE}),
    CharacterClass.EVOKER: frozenset({Role.HEALER, Role.DAMAGE}),
    CharacterClass.HUNTER: frozenset({Role.DAMAGE}),
    CharacterClass.MAGE: frozenset({Role.DAMAGE}),
    CharacterClass.MONK: frozenset({Role.TANK, Role.HEALER, Role.DAMAGE}),
    CharacterClass.PALADIN: frozenset({Role.TANK, Role.HEALER, Role.DAMAGE}),
    CharacterClass.PRIEST: frozenset({Role.HEALER, Role.DAMAGE}),
    CharacterClass.ROGUE: frozenset({Role.DAMAGE}),
    CharacterClass.SHAMAN: frozenset({Role.HEALER, Role.DAMAGE}),
    CharacterClass.WARLOCK: frozenset({Role.DAMAGE}),
    CharacterClass.WARRIOR: frozenset({Role.TANK, Role.DAMAGE}),
}

MAX_CHARACTER_LEVEL = 80


def role_is_valid_for_class(character_class: CharacterClass, role: Role) -> bool:
    """Return True when the class can perform the given role."""
    return role in CLASS_ROLES[character_class]


def default_role_for_class(character_class: CharacterClass) -> Role:
    """Pick a sensible default role for a class.

    Damage is preferred because every class can deal damage; the fallbacks
    exist only for completeness should Blizzard ever ship a class that
    cannot.
    """
    for candidate in (Role.DAMAGE, Role.TANK, Role.HEALER):
        if candidate in CLASS_ROLES[character_class]:
            return candidate
    raise ValueError(f"{character_class} has no valid roles configured.")
