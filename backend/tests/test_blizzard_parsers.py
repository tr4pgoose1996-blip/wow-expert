"""Tests for Blizzard JSON parsers.

These run against representative fixtures shaped like real Blizzard
responses, including the awkward cases: missing optional branches, locale
maps, and empty collections.
"""

from __future__ import annotations

from app.db.models.enums import CharacterClass, Faction, Role
from app.integrations.blizzard.constants import guild_slug, realm_slug
from app.integrations.blizzard.parsers import (
    localized,
    parse_account_profile,
    parse_achievements,
    parse_character_summary,
    parse_equipment,
    parse_guild,
    parse_guild_roster,
    parse_mounts,
    parse_pets,
    parse_professions,
    parse_reputations,
    parse_specializations,
)


class TestLocalized:
    def test_plain_string(self):
        assert localized("Argent Dawn") == "Argent Dawn"

    def test_name_wrapper(self):
        assert localized({"name": "Warrior", "id": 1}) == "Warrior"

    def test_locale_map(self):
        assert localized({"en_US": "Alliance", "fr_FR": "Alliance"}) == "Alliance"

    def test_nested_name_locale_map(self):
        assert localized({"name": {"en_US": "Protection"}}) == "Protection"

    def test_none(self):
        assert localized(None) is None

    def test_falls_back_to_any_string(self):
        assert localized({"de_DE": "Krieger"}) == "Krieger"


class TestSlugs:
    def test_spaces_become_hyphens(self):
        assert realm_slug("Argent Dawn") == "argent-dawn"

    def test_apostrophes_are_dropped(self):
        assert realm_slug("Azjol'Nerub") == "azjolnerub"

    def test_already_slugged_is_stable(self):
        assert realm_slug("argent-dawn") == "argent-dawn"

    def test_guild_slug(self):
        assert guild_slug("The Bloodsail Buccaneers") == (
            "the-bloodsail-buccaneers"
        )


class TestCharacterSummary:
    PAYLOAD = {
        "id": 12345,
        "name": "Grommash",
        "level": 80,
        "equipped_item_level": 623,
        "average_item_level": 618,
        "achievement_points": 15230,
        "faction": {"type": "HORDE", "name": "Horde"},
        "character_class": {"id": 1, "name": "Warrior"},
        "active_spec": {"id": 73, "name": "Protection"},
        "race": {"name": "Orc"},
        "realm": {"id": 1234, "slug": "argent-dawn", "name": "Argent Dawn"},
        "guild": {"id": 99, "name": "Warsong"},
        "last_login_timestamp": 1735689600000,
    }

    def test_core_fields(self):
        result = parse_character_summary(self.PAYLOAD)
        assert result["name"] == "Grommash"
        assert result["level"] == 80
        assert result["item_level"] == 623
        assert result["realm_slug"] == "argent-dawn"
        assert result["guild_name"] == "Warsong"

    def test_faction_and_class(self):
        result = parse_character_summary(self.PAYLOAD)
        assert result["faction"] is Faction.HORDE
        assert result["character_class"] is CharacterClass.WARRIOR

    def test_protection_spec_infers_tank(self):
        assert parse_character_summary(self.PAYLOAD)["primary_role"] is Role.TANK

    def test_restoration_spec_infers_healer(self):
        payload = {**self.PAYLOAD, "active_spec": {"name": "Restoration"}}
        assert parse_character_summary(payload)["primary_role"] is Role.HEALER

    def test_unknown_spec_defaults_to_damage(self):
        payload = {**self.PAYLOAD, "active_spec": {"name": "Fury"}}
        assert parse_character_summary(payload)["primary_role"] is Role.DAMAGE

    def test_last_login_parsed(self):
        result = parse_character_summary(self.PAYLOAD)
        assert result["last_login"] is not None
        assert result["last_login"].year == 2025

    def test_empty_payload_does_not_raise(self):
        result = parse_character_summary({})
        assert result["name"] is None
        assert result["faction"] is Faction.NEUTRAL
        assert result["character_class"] is None

    def test_missing_guild_is_none(self):
        payload = {k: v for k, v in self.PAYLOAD.items() if k != "guild"}
        assert parse_character_summary(payload)["guild_name"] is None


class TestEquipment:
    def test_parses_items_with_enchants_and_sockets(self):
        payload = {
            "equipped_items": [
                {
                    "item": {"id": 8888},
                    "slot": {"type": "HEAD", "name": "Head"},
                    "name": "Warhelm of the Warchief",
                    "quality": {"type": "EPIC"},
                    "level": {"value": 626},
                    "binding": {"type": "ON_ACQUIRE"},
                    "enchantments": [
                        {"display_string": "Enchanted: Stamina +100"}
                    ],
                    "sockets": [{"item": {"name": "Ruby"}}],
                    "set": {"item_set": {"name": "Warsong Regalia"}},
                }
            ]
        }
        items = parse_equipment(payload)
        assert len(items) == 1
        item = items[0]
        assert item["slot"] == "HEAD"
        assert item["item_level"] == 626
        assert item["quality"] == "EPIC"
        assert item["enchantments"] == ["Enchanted: Stamina +100"]
        assert item["sockets"] == ["Ruby"]
        assert item["set_name"] == "Warsong Regalia"

    def test_empty_equipment(self):
        assert parse_equipment({"equipped_items": []}) == []

    def test_missing_key(self):
        assert parse_equipment({}) == []

    def test_item_without_optional_branches(self):
        payload = {"equipped_items": [{"item": {"id": 1}, "name": "Rusty Axe"}]}
        item = parse_equipment(payload)[0]
        assert item["name"] == "Rusty Axe"
        assert item["enchantments"] == []
        assert item["item_level"] is None


class TestSpecializations:
    PAYLOAD = {
        "active_specialization": {"name": "Protection"},
        "specializations": [
            {
                "specialization": {"name": "Protection"},
                "loadouts": [
                    {
                        "is_active": True,
                        "talent_loadout_code": "CEEAAA",
                        "selected_class_talents": [
                            {
                                "rank": 1,
                                "tooltip": {
                                    "talent": {"id": 1, "name": "Shield Wall"}
                                },
                            }
                        ],
                        "selected_spec_talents": [
                            {
                                "rank": 2,
                                "tooltip": {
                                    "talent": {"id": 2, "name": "Last Stand"}
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }

    def test_active_spec(self):
        result = parse_specializations(self.PAYLOAD)
        assert result["active_specialization"] == "Protection"

    def test_active_loadout_selected(self):
        result = parse_specializations(self.PAYLOAD)
        assert result["active_loadout"] is not None
        assert result["active_loadout"]["code"] == "CEEAAA"

    def test_talents_extracted(self):
        loadout = parse_specializations(self.PAYLOAD)["loadouts"][0]
        assert loadout["class_talents"][0]["name"] == "Shield Wall"
        assert loadout["spec_talents"][0]["rank"] == 2

    def test_no_active_loadout(self):
        payload = {
            "specializations": [
                {
                    "specialization": {"name": "Arms"},
                    "loadouts": [{"is_active": False}],
                }
            ]
        }
        assert parse_specializations(payload)["active_loadout"] is None

    def test_empty(self):
        result = parse_specializations({})
        assert result["loadouts"] == []


class TestProfessions:
    def test_primary_and_secondary(self):
        payload = {
            "primaries": [
                {
                    "profession": {"id": 164, "name": "Blacksmithing"},
                    "tiers": [
                        {
                            "tier": {"name": "Khaz Algar Blacksmithing"},
                            "skill_points": 100,
                            "max_skill_points": 100,
                            "known_recipes": [{"id": 1}, {"id": 2}],
                        }
                    ],
                }
            ],
            "secondaries": [
                {"profession": {"id": 185, "name": "Cooking"}, "tiers": []}
            ],
        }
        result = parse_professions(payload)
        assert result["primaries"][0]["name"] == "Blacksmithing"
        assert result["primaries"][0]["max_tier_skill"] == 100
        assert result["primaries"][0]["tiers"][0]["known_recipes"] == 2
        assert result["secondaries"][0]["name"] == "Cooking"

    def test_empty(self):
        result = parse_professions({})
        assert result == {"primaries": [], "secondaries": []}


class TestAchievements:
    def test_only_completed_are_kept(self):
        payload = {
            "total_points": 15230,
            "achievements": [
                {
                    "achievement": {"id": 1, "name": "Level 80"},
                    "completed_timestamp": 1735689600000,
                },
                {"achievement": {"id": 2, "name": "Incomplete"}},
            ],
        }
        result = parse_achievements(payload)
        assert result["total_completed"] == 1
        assert result["total_points"] == 15230
        assert result["recent"][0]["name"] == "Level 80"

    def test_sorted_most_recent_first(self):
        payload = {
            "achievements": [
                {
                    "achievement": {"id": 1, "name": "Older"},
                    "completed_timestamp": 1600000000000,
                },
                {
                    "achievement": {"id": 2, "name": "Newer"},
                    "completed_timestamp": 1735689600000,
                },
            ]
        }
        recent = parse_achievements(payload)["recent"]
        assert [a["name"] for a in recent] == ["Newer", "Older"]

    def test_recent_is_capped_at_100(self):
        payload = {
            "achievements": [
                {
                    "achievement": {"id": i, "name": f"Ach {i}"},
                    "completed_timestamp": 1600000000000 + i * 1000,
                }
                for i in range(250)
            ]
        }
        result = parse_achievements(payload)
        assert result["total_completed"] == 250
        assert len(result["recent"]) == 100


class TestReputations:
    def test_percent_computed(self):
        payload = {
            "reputations": [
                {
                    "faction": {"id": 2600, "name": "The Assembly"},
                    "standing": {
                        "value": 4200,
                        "max": 8400,
                        "name": "Friendly",
                    },
                }
            ]
        }
        result = parse_reputations(payload)
        assert result[0]["percent"] == 50.0
        assert result[0]["faction"] == "The Assembly"

    def test_zero_max_gives_no_percent(self):
        payload = {
            "reputations": [
                {"faction": {"name": "X"}, "standing": {"value": 0, "max": 0}}
            ]
        }
        assert parse_reputations(payload)[0]["percent"] is None

    def test_empty(self):
        assert parse_reputations({}) == []


class TestCollections:
    def test_mounts_sorted_by_name(self):
        payload = {
            "mounts": [
                {"mount": {"id": 2, "name": "Zulian Tiger"}},
                {"mount": {"id": 1, "name": "Albino Drake"}, "is_favorite": True},
            ]
        }
        result = parse_mounts(payload)
        assert result["total"] == 2
        assert result["mounts"][0]["name"] == "Albino Drake"
        assert result["mounts"][0]["is_favorite"] is True

    def test_pets_sorted_by_level_desc(self):
        payload = {
            "pets": [
                {"id": 1, "species": {"id": 10, "name": "Rat"}, "level": 1},
                {"id": 2, "species": {"id": 20, "name": "Mechanical Squirrel"},
                 "level": 25, "quality": {"type": "RARE"}},
            ]
        }
        result = parse_pets(payload)
        assert result["max_level"] == 25
        assert result["max_level_count"] == 1
        assert result["pets"][0]["level"] == 25

    def test_empty_collections(self):
        assert parse_mounts({})["total"] == 0
        assert parse_pets({})["total"] == 0


class TestGuild:
    def test_guild_summary(self):
        payload = {
            "id": 99,
            "name": "Warsong",
            "faction": {"type": "HORDE"},
            "realm": {"name": "Argent Dawn", "slug": "argent-dawn"},
            "member_count": 120,
            "achievement_points": 3400,
            "created_timestamp": 1400000000000,
        }
        result = parse_guild(payload)
        assert result["name"] == "Warsong"
        assert result["faction"] is Faction.HORDE
        assert result["member_count"] == 120
        assert result["created"] is not None

    def test_roster_sorted_by_rank(self):
        payload = {
            "members": [
                {
                    "character": {"name": "Grunt", "level": 80,
                                  "realm": {"slug": "argent-dawn"}},
                    "rank": 5,
                },
                {
                    "character": {"name": "Warchief", "level": 80,
                                  "realm": {"slug": "argent-dawn"}},
                    "rank": 0,
                },
            ]
        }
        members = parse_guild_roster(payload)
        assert members[0]["name"] == "Warchief"
        assert members[0]["rank"] == 0

    def test_empty_roster(self):
        assert parse_guild_roster({}) == []


class TestAccountProfile:
    def test_flattens_accounts(self):
        payload = {
            "wow_accounts": [
                {
                    "id": 1,
                    "characters": [
                        {
                            "id": 100,
                            "name": "Grommash",
                            "level": 80,
                            "faction": {"type": "HORDE"},
                            "playable_class": {"name": "Warrior"},
                            "character_class": {"name": "Warrior"},
                            "realm": {"id": 1, "slug": "argent-dawn",
                                      "name": "Argent Dawn"},
                        },
                        {
                            "id": 101,
                            "name": "Jaina",
                            "level": 70,
                            "faction": {"type": "ALLIANCE"},
                            "character_class": {"name": "Mage"},
                            "realm": {"id": 2, "slug": "silvermoon",
                                      "name": "Silvermoon"},
                        },
                    ],
                }
            ]
        }
        characters = parse_account_profile(payload)
        assert len(characters) == 2
        # Sorted by level descending.
        assert characters[0]["name"] == "Grommash"
        assert characters[0]["character_class"] is CharacterClass.WARRIOR
        assert characters[1]["character_class"] is CharacterClass.MAGE
        assert characters[0]["wow_account_id"] == 1

    def test_empty(self):
        assert parse_account_profile({}) == []

    def test_unknown_class_yields_none(self):
        payload = {
            "wow_accounts": [
                {
                    "id": 1,
                    "characters": [
                        {
                            "id": 1, "name": "Mystery", "level": 10,
                            "faction": {"type": "HORDE"},
                            "character_class": {"name": "Tinkerer"},
                            "realm": {"slug": "test"},
                        }
                    ],
                }
            ]
        }
        assert parse_account_profile(payload)[0]["character_class"] is None
