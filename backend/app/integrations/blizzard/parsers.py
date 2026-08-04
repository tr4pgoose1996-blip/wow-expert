"""Pure functions translating Blizzard JSON into internal shapes.

Blizzard responses are deeply nested, inconsistently populated, and change
between expansions. Isolating every ``payload["a"]["b"]`` access here means
an upstream shape change is fixed in one file, and each parser is trivially
unit-testable without network access.

Every parser is defensive: missing optional branches yield ``None`` or an
empty list rather than raising, because a partially-populated profile is
far more useful than a failed import.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db.models.enums import CharacterClass, Faction, Role

# Blizzard class names -> internal enum.
_CLASS_MAP: dict[str, CharacterClass] = {
    "death knight": CharacterClass.DEATH_KNIGHT,
    "demon hunter": CharacterClass.DEMON_HUNTER,
    "druid": CharacterClass.DRUID,
    "evoker": CharacterClass.EVOKER,
    "hunter": CharacterClass.HUNTER,
    "mage": CharacterClass.MAGE,
    "monk": CharacterClass.MONK,
    "paladin": CharacterClass.PALADIN,
    "priest": CharacterClass.PRIEST,
    "rogue": CharacterClass.ROGUE,
    "shaman": CharacterClass.SHAMAN,
    "warlock": CharacterClass.WARLOCK,
    "warrior": CharacterClass.WARRIOR,
}

_ROLE_MAP: dict[str, Role] = {
    "TANK": Role.TANK,
    "HEALER": Role.HEALER,
    "DAMAGE": Role.DAMAGE,
    "DAMAGER": Role.DAMAGE,
}

# Specialisations whose role cannot be inferred from the class alone.
_SPEC_ROLES: dict[str, Role] = {
    "blood": Role.TANK, "protection": Role.TANK, "guardian": Role.TANK,
    "brewmaster": Role.TANK, "vengeance": Role.TANK,
    "restoration": Role.HEALER, "holy": Role.HEALER, "discipline": Role.HEALER,
    "mistweaver": Role.HEALER, "preservation": Role.HEALER,
}


def localized(value: Any) -> str | None:
    """Extract a display string from a Blizzard localized field.

    Handles the three shapes Blizzard uses: a bare string, a
    ``{"name": ...}`` wrapper, and a locale map.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "name" in value:
            return localized(value["name"])
        for key in ("en_US", "en_GB"):
            if key in value:
                return value[key]
        for candidate in value.values():
            if isinstance(candidate, str):
                return candidate
    return None


def _timestamp(value: Any) -> datetime | None:
    """Convert a Blizzard millisecond epoch to an aware datetime."""
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None


def parse_faction(payload: dict[str, Any]) -> Faction:
    raw = (payload.get("faction") or {}).get("type", "")
    return {"ALLIANCE": Faction.ALLIANCE, "HORDE": Faction.HORDE}.get(
        str(raw).upper(), Faction.NEUTRAL
    )


def parse_class(payload: dict[str, Any]) -> CharacterClass | None:
    name = localized(payload.get("character_class"))
    if not name:
        return None
    return _CLASS_MAP.get(name.strip().lower())


def parse_role(payload: dict[str, Any]) -> Role:
    """Infer the character's role from its active specialisation."""
    spec = localized(payload.get("active_spec"))
    if spec:
        role = _SPEC_ROLES.get(spec.strip().lower())
        if role is not None:
            return role
    return Role.DAMAGE


def parse_character_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse ``/profile/wow/character/{realm}/{name}``."""
    realm = payload.get("realm") or {}
    guild = payload.get("guild") or {}

    return {
        "blizzard_id": payload.get("id"),
        "name": payload.get("name"),
        "realm": localized(realm) or realm.get("slug"),
        "realm_slug": realm.get("slug"),
        "realm_id": realm.get("id"),
        "level": payload.get("level"),
        "faction": parse_faction(payload),
        "character_class": parse_class(payload),
        "primary_role": parse_role(payload),
        "specialization": localized(payload.get("active_spec")),
        "race": localized(payload.get("race")),
        "gender": localized(payload.get("gender")),
        "item_level": payload.get("equipped_item_level") or 0,
        "average_item_level": payload.get("average_item_level") or 0,
        "achievement_points": payload.get("achievement_points") or 0,
        "guild_name": localized(guild) if guild else None,
        "guild_id": guild.get("id") if guild else None,
        "covenant": localized(payload.get("covenant_progress")),
        "title": localized(payload.get("active_title")),
        "last_login": _timestamp(payload.get("last_login_timestamp")),
    }


def parse_equipment(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse ``/equipment`` into one record per equipped slot."""
    items: list[dict[str, Any]] = []

    for entry in payload.get("equipped_items") or []:
        if not isinstance(entry, dict):
            continue
        item = entry.get("item") or {}
        enchantments = [
            localized(e.get("display_string"))
            for e in entry.get("enchantments") or []
            if isinstance(e, dict)
        ]
        sockets = [
            localized(s.get("item") or {})
            for s in entry.get("sockets") or []
            if isinstance(s, dict) and s.get("item")
        ]

        items.append(
            {
                "slot": (entry.get("slot") or {}).get("type"),
                "slot_name": localized(entry.get("slot")),
                "item_id": item.get("id"),
                "name": localized(entry.get("name")),
                "quality": (entry.get("quality") or {}).get("type"),
                "item_level": (entry.get("level") or {}).get("value"),
                "bound": (entry.get("binding") or {}).get("type"),
                "enchantments": [e for e in enchantments if e],
                "sockets": [s for s in sockets if s],
                "set_name": localized((entry.get("set") or {}).get("item_set")),
                "transmog": localized(
                    (entry.get("transmog") or {}).get("item")
                ),
            }
        )

    return items


def parse_specializations(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse ``/specializations`` into active spec plus talent loadouts."""
    active_spec = localized(payload.get("active_specialization"))
    loadouts: list[dict[str, Any]] = []

    for spec in payload.get("specializations") or []:
        if not isinstance(spec, dict):
            continue
        spec_name = localized(spec.get("specialization"))

        for loadout in spec.get("loadouts") or []:
            if not isinstance(loadout, dict):
                continue

            def _talents(
                key: str, loadout: dict[str, Any] = loadout
            ) -> list[dict[str, Any]]:
                # loadout is bound as a default argument so the closure keeps
                # the current iteration's value rather than the loop's last.
                result = []
                for node in loadout.get(key) or []:
                    if not isinstance(node, dict):
                        continue
                    tooltip = node.get("tooltip") or {}
                    talent = tooltip.get("talent") or {}
                    result.append(
                        {
                            "id": talent.get("id"),
                            "name": localized(talent),
                            "rank": node.get("rank"),
                        }
                    )
                return result

            loadouts.append(
                {
                    "specialization": spec_name,
                    "is_active": bool(loadout.get("is_active")),
                    "code": loadout.get("talent_loadout_code"),
                    "class_talents": _talents("selected_class_talents"),
                    "spec_talents": _talents("selected_spec_talents"),
                    "hero_talents": _talents("selected_hero_talents"),
                }
            )

    return {
        "active_specialization": active_spec,
        "loadouts": loadouts,
        "active_loadout": next(
            (loadout for loadout in loadouts if loadout["is_active"]), None
        ),
    }


def parse_professions(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse ``/professions`` into primary and secondary skill lists."""

    def _extract(key: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for entry in payload.get(key) or []:
            if not isinstance(entry, dict):
                continue
            tiers = []
            for tier in entry.get("tiers") or []:
                if not isinstance(tier, dict):
                    continue
                tiers.append(
                    {
                        "name": localized(tier.get("tier")),
                        "skill_points": tier.get("skill_points") or 0,
                        "max_skill_points": tier.get("max_skill_points") or 0,
                        "known_recipes": len(tier.get("known_recipes") or []),
                    }
                )
            result.append(
                {
                    "id": (entry.get("profession") or {}).get("id"),
                    "name": localized(entry.get("profession")),
                    "tiers": tiers,
                    "max_tier_skill": max(
                        (t["skill_points"] for t in tiers), default=0
                    ),
                }
            )
        return result

    return {
        "primaries": _extract("primaries"),
        "secondaries": _extract("secondaries"),
    }


def parse_achievements(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse ``/achievements``.

    Only completed achievements are retained, and the full list is capped —
    an established account can hold thousands of entries, and storing all of
    them per character would bloat the row without adding coaching value.
    """
    completed: list[dict[str, Any]] = []

    for entry in payload.get("achievements") or []:
        if not isinstance(entry, dict):
            continue
        completed_at = _timestamp(entry.get("completed_timestamp"))
        if completed_at is None:
            continue
        completed.append(
            {
                "id": (entry.get("achievement") or {}).get("id"),
                "name": localized(entry.get("achievement")),
                "completed_at": completed_at.isoformat(),
            }
        )

    completed.sort(key=lambda a: a["completed_at"], reverse=True)

    return {
        "total_points": payload.get("total_points") or 0,
        "total_completed": len(completed),
        "recent": completed[:100],
    }


def parse_reputations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse ``/reputations`` into a flat standing list."""
    reputations: list[dict[str, Any]] = []

    for entry in payload.get("reputations") or []:
        if not isinstance(entry, dict):
            continue
        standing = entry.get("standing") or {}
        value = standing.get("value") or 0
        maximum = standing.get("max") or 0

        reputations.append(
            {
                "faction_id": (entry.get("faction") or {}).get("id"),
                "faction": localized(entry.get("faction")),
                "standing": localized(standing.get("name"))
                or standing.get("tier"),
                "value": value,
                "max": maximum,
                "percent": round(value / maximum * 100, 1) if maximum else None,
                "is_paragon": bool(standing.get("paragon")),
            }
        )

    return reputations


def parse_mounts(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse a mount collection summary (character or account scope)."""
    mounts: list[dict[str, Any]] = []

    for entry in payload.get("mounts") or []:
        if not isinstance(entry, dict):
            continue
        mount = entry.get("mount") or {}
        mounts.append(
            {
                "id": mount.get("id"),
                "name": localized(mount),
                "is_useable": entry.get("is_useable", True),
                "is_favorite": bool(entry.get("is_favorite")),
            }
        )

    mounts.sort(key=lambda m: (m["name"] or "").lower())
    return {"total": len(mounts), "mounts": mounts}


def parse_pets(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse a battle pet collection summary."""
    pets: list[dict[str, Any]] = []
    max_level = 0

    for entry in payload.get("pets") or []:
        if not isinstance(entry, dict):
            continue
        species = entry.get("species") or {}
        level = entry.get("level") or 1
        max_level = max(max_level, level)

        pets.append(
            {
                "id": entry.get("id"),
                "species_id": species.get("id"),
                "name": entry.get("name") or localized(species),
                "level": level,
                "quality": (entry.get("quality") or {}).get("type"),
                "is_favorite": bool(entry.get("is_favorite")),
            }
        )

    pets.sort(key=lambda p: (-p["level"], (p["name"] or "").lower()))
    return {
        "total": len(pets),
        "max_level": max_level,
        "max_level_count": sum(1 for p in pets if p["level"] == 25),
        "pets": pets,
    }


def parse_realm_search(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse ``/data/wow/search/realm`` results."""
    realms: list[dict[str, Any]] = []

    for result in payload.get("results") or []:
        data = (result or {}).get("data") or {}
        realms.append(
            {
                "id": data.get("id"),
                "name": localized(data.get("name")),
                "slug": data.get("slug"),
                "region": localized((data.get("region") or {}).get("name")),
                "category": localized(data.get("category")),
                "timezone": data.get("timezone"),
                "type": localized((data.get("type") or {}).get("name"))
                or (data.get("type") or {}).get("type"),
                "is_tournament": bool(data.get("is_tournament")),
                "locale": data.get("locale"),
            }
        )

    return realms


def parse_realm_index(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse ``/data/wow/realm/index``."""
    return [
        {
            "id": realm.get("id"),
            "name": localized(realm.get("name")),
            "slug": realm.get("slug"),
        }
        for realm in payload.get("realms") or []
        if isinstance(realm, dict)
    ]


def parse_guild(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse ``/data/wow/guild/{realm}/{guild}``."""
    realm = payload.get("realm") or {}
    return {
        "id": payload.get("id"),
        "name": payload.get("name"),
        "faction": parse_faction(payload),
        "realm": localized(realm),
        "realm_slug": realm.get("slug"),
        "member_count": payload.get("member_count") or 0,
        "achievement_points": payload.get("achievement_points") or 0,
        "created": _timestamp(payload.get("created_timestamp")),
    }


def parse_guild_roster(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse ``/roster``, sorted by guild rank then level."""
    members: list[dict[str, Any]] = []

    for entry in payload.get("members") or []:
        if not isinstance(entry, dict):
            continue
        character = entry.get("character") or {}
        members.append(
            {
                "name": character.get("name"),
                "id": character.get("id"),
                "level": character.get("level"),
                "rank": entry.get("rank"),
                "realm_slug": (character.get("realm") or {}).get("slug"),
                "character_class": localized(
                    character.get("playable_class") or {}
                ),
                "race": localized(character.get("playable_race") or {}),
            }
        )

    # Rank 0 is the guild master, so compare against None explicitly:
    # a falsy check would sort the highest rank to the bottom.
    members.sort(
        key=lambda m: (
            m["rank"] if m["rank"] is not None else 99,
            -(m["level"] or 0),
        )
    )
    return members


def parse_account_profile(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten ``/profile/user/wow`` into a list of characters.

    The response nests characters under WoW accounts; callers want one flat
    roster, annotated with which account each character belongs to.
    """
    characters: list[dict[str, Any]] = []

    for account in payload.get("wow_accounts") or []:
        if not isinstance(account, dict):
            continue
        account_id = account.get("id")

        for character in account.get("characters") or []:
            if not isinstance(character, dict):
                continue
            realm = character.get("realm") or {}
            characters.append(
                {
                    "wow_account_id": account_id,
                    "blizzard_id": character.get("id"),
                    "name": character.get("name"),
                    "realm": localized(realm),
                    "realm_slug": realm.get("slug"),
                    "realm_id": realm.get("id"),
                    "level": character.get("level") or 1,
                    "faction": parse_faction(character),
                    "character_class": parse_class(character),
                    "race": localized(character.get("playable_race")),
                    "gender": localized(character.get("gender")),
                    "protected_url": (
                        (character.get("protected_character") or {})
                        .get("href")
                    ),
                }
            )

    characters.sort(key=lambda c: (-(c["level"] or 0), c["name"] or ""))
    return characters
