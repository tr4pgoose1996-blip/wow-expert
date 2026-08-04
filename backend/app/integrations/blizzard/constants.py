"""Blizzard API constants: namespaces, regions, and endpoint paths."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

REGIONS: Final[frozenset[str]] = frozenset({"us", "eu", "kr", "tw", "cn"})

DEFAULT_LOCALES: Final[dict[str, str]] = {
    "us": "en_US",
    "eu": "en_GB",
    "kr": "ko_KR",
    "tw": "zh_TW",
    "cn": "zh_CN",
}


class Namespace(StrEnum):
    """Blizzard namespace categories.

    Every WoW API request must carry a namespace of the form
    ``{category}-{region}``. ``static`` holds versioned game data, ``dynamic``
    holds data that changes with the game world, and ``profile`` holds
    player-specific data.
    """

    STATIC = "static"
    DYNAMIC = "dynamic"
    PROFILE = "profile"

    def for_region(self, region: str) -> str:
        return f"{self.value}-{region}"


def api_host(region: str) -> str:
    """Return the regional API host."""
    if region == "cn":
        return "https://gateway.battlenet.com.cn"
    return f"https://{region}.api.blizzard.com"


class Endpoints:
    """Endpoint path builders.

    Centralising paths means a Blizzard-side change is a one-line edit here
    rather than a hunt through the service layer.
    """

    # -- Account (requires user OAuth with wow.profile) --------------------
    ACCOUNT_PROFILE = "/profile/user/wow"
    ACCOUNT_COLLECTIONS = "/profile/user/wow/collections"
    ACCOUNT_MOUNTS = "/profile/user/wow/collections/mounts"
    ACCOUNT_PETS = "/profile/user/wow/collections/pets"
    ACCOUNT_TOYS = "/profile/user/wow/collections/toys"
    ACCOUNT_HEIRLOOMS = "/profile/user/wow/collections/heirlooms"
    ACCOUNT_TRANSMOGS = "/profile/user/wow/collections/transmogs"
    ACCOUNT_TITLES = "/profile/user/wow/collections/titles"
    ACCOUNT_TABARDS = "/profile/user/wow/collections/tabards"
    ACCOUNT_APPEARANCES = "/profile/user/wow/collections/appearances"

    @staticmethod
    def protected_character(realm_id: int, character_id: int) -> str:
        return f"/profile/user/wow/protected-character/{realm_id}-{character_id}"

    # -- Character profile -------------------------------------------------
    @staticmethod
    def character(realm_slug: str, name: str) -> str:
        return f"/profile/wow/character/{realm_slug}/{name.lower()}"

    @classmethod
    def character_equipment(cls, realm_slug: str, name: str) -> str:
        return f"{cls.character(realm_slug, name)}/equipment"

    @classmethod
    def character_specializations(cls, realm_slug: str, name: str) -> str:
        return f"{cls.character(realm_slug, name)}/specializations"

    @classmethod
    def character_professions(cls, realm_slug: str, name: str) -> str:
        return f"{cls.character(realm_slug, name)}/professions"

    @classmethod
    def character_achievements(cls, realm_slug: str, name: str) -> str:
        return f"{cls.character(realm_slug, name)}/achievements"

    @classmethod
    def character_reputations(cls, realm_slug: str, name: str) -> str:
        return f"{cls.character(realm_slug, name)}/reputations"

    @classmethod
    def character_mounts(cls, realm_slug: str, name: str) -> str:
        return f"{cls.character(realm_slug, name)}/collections/mounts"

    @classmethod
    def character_pets(cls, realm_slug: str, name: str) -> str:
        return f"{cls.character(realm_slug, name)}/collections/pets"

    @classmethod
    def character_statistics(cls, realm_slug: str, name: str) -> str:
        return f"{cls.character(realm_slug, name)}/statistics"

    @classmethod
    def character_titles(cls, realm_slug: str, name: str) -> str:
        return f"{cls.character(realm_slug, name)}/titles"

    @classmethod
    def character_media(cls, realm_slug: str, name: str) -> str:
        return f"{cls.character(realm_slug, name)}/character-media"

    @classmethod
    def character_raids(cls, realm_slug: str, name: str) -> str:
        return f"{cls.character(realm_slug, name)}/encounters/raids"

    @classmethod
    def character_dungeons(cls, realm_slug: str, name: str) -> str:
        return f"{cls.character(realm_slug, name)}/encounters/dungeons"

    @classmethod
    def character_mythic_keystone(cls, realm_slug: str, name: str) -> str:
        return f"{cls.character(realm_slug, name)}/mythic-keystone-profile"

    # -- Guild -------------------------------------------------------------
    @staticmethod
    def guild(realm_slug: str, guild_slug: str) -> str:
        return f"/data/wow/guild/{realm_slug}/{guild_slug}"

    @classmethod
    def guild_roster(cls, realm_slug: str, guild_slug: str) -> str:
        return f"{cls.guild(realm_slug, guild_slug)}/roster"

    @classmethod
    def guild_achievements(cls, realm_slug: str, guild_slug: str) -> str:
        return f"{cls.guild(realm_slug, guild_slug)}/achievements"

    @classmethod
    def guild_activity(cls, realm_slug: str, guild_slug: str) -> str:
        return f"{cls.guild(realm_slug, guild_slug)}/activity"

    # -- Realms ------------------------------------------------------------
    REALM_INDEX = "/data/wow/realm/index"
    REALM_SEARCH = "/data/wow/search/realm"
    CONNECTED_REALM_INDEX = "/data/wow/connected-realm/index"
    CONNECTED_REALM_SEARCH = "/data/wow/search/connected-realm"

    @staticmethod
    def realm(realm_slug: str) -> str:
        return f"/data/wow/realm/{realm_slug}"

    # -- Static game data --------------------------------------------------
    MOUNT_INDEX = "/data/wow/mount/index"
    MOUNT_SEARCH = "/data/wow/search/mount"
    PET_INDEX = "/data/wow/pet/index"
    ACHIEVEMENT_INDEX = "/data/wow/achievement/index"
    PROFESSION_INDEX = "/data/wow/profession/index"
    PLAYABLE_CLASS_INDEX = "/data/wow/playable-class/index"

    @staticmethod
    def mount(mount_id: int) -> str:
        return f"/data/wow/mount/{mount_id}"

    @staticmethod
    def pet(pet_id: int) -> str:
        return f"/data/wow/pet/{pet_id}"

    @staticmethod
    def achievement(achievement_id: int) -> str:
        return f"/data/wow/achievement/{achievement_id}"

    # -- Journal (encounters / instances) ----------------------------------
    JOURNAL_INSTANCE_INDEX = "/data/wow/journal-instance/index"
    JOURNAL_ENCOUNTER_INDEX = "/data/wow/journal-encounter/index"

    @staticmethod
    def journal_instance(instance_id: int) -> str:
        return f"/data/wow/journal-instance/{instance_id}"

    @staticmethod
    def journal_encounter(encounter_id: int) -> str:
        return f"/data/wow/journal-encounter/{encounter_id}"

    @staticmethod
    def media_journal_instance(instance_id: int) -> str:
        return f"/data/wow/media/journal-instance/{instance_id}"


def realm_slug(realm: str) -> str:
    """Convert a human realm name to Blizzard's slug form.

    ``"Argent Dawn"`` -> ``"argent-dawn"``; ``"Azjol'Nerub"`` ->
    ``"azjolnerub"``. Apostrophes are dropped rather than replaced, matching
    Blizzard's own slugging.
    """
    return (
        realm.strip()
        .lower()
        .replace("'", "")
        .replace("'", "")
        .replace(" ", "-")
        .replace("_", "-")
    )


def guild_slug(guild_name: str) -> str:
    """Convert a guild name to its slug form."""
    return realm_slug(guild_name)
