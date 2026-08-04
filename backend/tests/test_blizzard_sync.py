"""Tests for character import and synchronization."""

from __future__ import annotations

from datetime import UTC

import httpx
import pytest

from app.core.crypto import encrypt_token
from app.core.exceptions import ConflictError, NotFoundError
from app.db.models.blizzard import BlizzardAccount
from app.db.models.character import Character
from app.db.models.enums import (
    CharacterClass,
    ContentFocus,
    Faction,
    Role,
    SyncScope,
    SyncStatus,
)
from app.db.models.user import User
from app.integrations.blizzard.client import BlizzardAPIClient
from app.integrations.blizzard.constants import api_host
from app.integrations.blizzard.oauth import BlizzardOAuthClient, OAuthToken
from app.services.blizzard_sync import BlizzardSyncService

ACCOUNT_PROFILE = {
    "wow_accounts": [
        {
            "id": 1,
            "characters": [
                {
                    "id": 100,
                    "name": "Grommash",
                    "level": 80,
                    "faction": {"type": "HORDE"},
                    "character_class": {"name": "Warrior"},
                    "playable_race": {"name": "Orc"},
                    "realm": {"id": 1, "slug": "argent-dawn",
                              "name": "Argent Dawn"},
                },
                {
                    "id": 101,
                    "name": "Lowbie",
                    "level": 5,
                    "faction": {"type": "HORDE"},
                    "character_class": {"name": "Rogue"},
                    "realm": {"id": 1, "slug": "argent-dawn",
                              "name": "Argent Dawn"},
                },
            ],
        }
    ]
}

CHARACTER_SUMMARY = {
    "id": 100,
    "name": "Grommash",
    "level": 80,
    "equipped_item_level": 623,
    "achievement_points": 15230,
    "faction": {"type": "HORDE"},
    "character_class": {"name": "Warrior"},
    "active_spec": {"name": "Protection"},
    "race": {"name": "Orc"},
    "realm": {"slug": "argent-dawn", "name": "Argent Dawn"},
    "guild": {"name": "Warsong"},
}


def routing_handler(routes: dict[str, dict], missing: set[str] | None = None):
    """Build a mock transport that maps URL suffixes to JSON bodies."""
    missing = missing or set()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for suffix in missing:
            if path.endswith(suffix):
                return httpx.Response(404)
        for suffix, body in routes.items():
            if path.endswith(suffix):
                return httpx.Response(200, json=body)
        return httpx.Response(404)

    return handler


class NoCache:
    async def get(self, key):
        return None

    async def set(self, key, value, ttl=None):
        return True

    async def delete(self, *keys):
        return 0

    async def delete_prefix(self, prefix):
        return 0


def build_service(session, handler) -> BlizzardSyncService:
    oauth = BlizzardOAuthClient(httpx.AsyncClient())
    oauth._app_token = OAuthToken("app-token", expires_at=9_999_999_999)

    client = BlizzardAPIClient(
        oauth=oauth,
        cache=NoCache(),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url=api_host("us")
        ),
        region="us",
    )
    return BlizzardSyncService(session, client)


@pytest.fixture
async def account(session) -> BlizzardAccount:
    from datetime import datetime, timedelta

    user = User(email="t@example.com", username="tester", hashed_password="x")
    session.add(user)
    await session.flush()

    linked = BlizzardAccount(
        user_id=user.id,
        battlenet_id=555,
        battletag="Thrall#1234",
        region="us",
        access_token_encrypted=encrypt_token("valid-user-token"),
        token_expires_at=datetime.now(UTC) + timedelta(hours=10),
        scopes="openid wow.profile",
    )
    session.add(linked)
    await session.flush()
    return linked


class TestRosterFetch:
    async def test_fetches_and_flattens(self, session, account):
        service = build_service(
            session, routing_handler({"/profile/user/wow": ACCOUNT_PROFILE})
        )
        roster = await service.fetch_account_roster(account)

        assert len(roster) == 2
        assert roster[0]["name"] == "Grommash"
        assert roster[0]["character_class"] is CharacterClass.WARRIOR

    async def test_empty_roster(self, session, account):
        service = build_service(
            session, routing_handler({"/profile/user/wow": {}})
        )
        assert await service.fetch_account_roster(account) == []


class TestImport:
    async def test_imports_above_min_level(self, session, account):
        service = build_service(
            session, routing_handler({"/profile/user/wow": ACCOUNT_PROFILE})
        )
        result = await service.import_characters(account, min_level=10)

        assert result["imported"] == 1
        assert result["skipped"] == 1  # the level-5 rogue
        assert result["characters"][0]["name"] == "Grommash"

    async def test_min_level_one_imports_all(self, session, account):
        service = build_service(
            session, routing_handler({"/profile/user/wow": ACCOUNT_PROFILE})
        )
        result = await service.import_characters(account, min_level=1)
        assert result["imported"] == 2

    async def test_selective_import_by_id(self, session, account):
        service = build_service(
            session, routing_handler({"/profile/user/wow": ACCOUNT_PROFILE})
        )
        result = await service.import_characters(
            account, blizzard_ids=[101], min_level=1
        )
        assert result["imported"] == 1
        assert result["characters"][0]["name"] == "Lowbie"

    async def test_reimport_updates_instead_of_duplicating(
        self, session, account
    ):
        service = build_service(
            session, routing_handler({"/profile/user/wow": ACCOUNT_PROFILE})
        )
        await service.import_characters(account, min_level=10)
        second = await service.import_characters(account, min_level=10)

        assert second["imported"] == 0
        assert second["updated"] == 1

    async def test_import_preserves_user_authored_fields(
        self, session, account
    ):
        """Goals and focus are the user's, and must survive a re-import."""
        service = build_service(
            session, routing_handler({"/profile/user/wow": ACCOUNT_PROFILE})
        )
        await service.import_characters(account, min_level=10)

        character = await service.characters.get_by_identity(
            account.user_id, "Grommash", "Argent Dawn"
        )
        await service.characters.update(
            character,
            goals="Push to +15 keys.",
            content_focus=ContentFocus.MYTHIC_PLUS,
        )

        await service.import_characters(account, min_level=10)
        await session.refresh(character)

        assert character.goals == "Push to +15 keys."
        assert character.content_focus is ContentFocus.MYTHIC_PLUS

    async def test_updates_last_sync_timestamp(self, session, account):
        service = build_service(
            session, routing_handler({"/profile/user/wow": ACCOUNT_PROFILE})
        )
        assert account.last_sync_at is None
        await service.import_characters(account)
        assert account.last_sync_at is not None

    async def test_unknown_class_reported_as_error(self, session, account):
        payload = {
            "wow_accounts": [
                {
                    "id": 1,
                    "characters": [
                        {
                            "id": 1, "name": "Mystery", "level": 80,
                            "faction": {"type": "HORDE"},
                            "character_class": {"name": "Tinkerer"},
                            "realm": {"slug": "test", "name": "Test"},
                        }
                    ],
                }
            ]
        }
        service = build_service(
            session, routing_handler({"/profile/user/wow": payload})
        )
        result = await service.import_characters(account)

        assert result["imported"] == 0
        assert "Unrecognised" in result["errors"][0]["error"]


class TestCharacterSync:
    @pytest.fixture
    async def character(self, session, account) -> Character:
        entity = Character(
            owner_id=account.user_id,
            name="Grommash",
            realm="Argent Dawn",
            region="us",
            faction=Faction.HORDE,
            character_class=CharacterClass.WARRIOR,
            primary_role=Role.TANK,
            level=70,
            external_data={"realm_slug": "argent-dawn"},
        )
        session.add(entity)
        await session.flush()
        return entity

    async def test_full_sync_populates_every_scope(
        self, session, character
    ):
        routes = {
            "/equipment": {"equipped_items": [
                {"item": {"id": 1}, "name": "Axe",
                 "slot": {"type": "MAIN_HAND"}, "level": {"value": 626}}
            ]},
            "/specializations": {
                "active_specialization": {"name": "Protection"},
                "specializations": [],
            },
            "/professions": {"primaries": [
                {"profession": {"id": 164, "name": "Blacksmithing"},
                 "tiers": []}
            ]},
            "/achievements": {"total_points": 15230, "achievements": []},
            "/reputations": {"reputations": []},
            "/collections/mounts": {"mounts": [
                {"mount": {"id": 1, "name": "Drake"}}
            ]},
            "/collections/pets": {"pets": []},
            "/character/argent-dawn/grommash": CHARACTER_SUMMARY,
        }
        service = build_service(session, routing_handler(routes))
        result = await service.sync_character(character)

        assert result["status"] == SyncStatus.SUCCESS.value
        assert set(result["synced"]) == {
            "profile", "equipment", "talents", "professions",
            "achievements", "reputations", "mounts", "pets",
        }

        snapshot = await service.snapshots.get_for_character(character.id)
        assert snapshot.equipment["count"] == 1
        assert snapshot.mounts["total"] == 1
        assert snapshot.guild_name == "Warsong"

    async def test_profile_promotes_fields_onto_character(
        self, session, character
    ):
        service = build_service(
            session,
            routing_handler({"/character/argent-dawn/grommash": CHARACTER_SUMMARY}),
        )
        await service.sync_character(character, scopes=(SyncScope.PROFILE,))
        await session.refresh(character)

        assert character.level == 80
        assert character.item_level == 623
        assert character.specialization == "Protection"

    async def test_partial_sync_when_one_scope_fails(
        self, session, character
    ):
        routes = {
            "/character/argent-dawn/grommash": CHARACTER_SUMMARY,
            "/equipment": {"equipped_items": []},
        }
        service = build_service(
            session, routing_handler(routes, missing={"/professions"})
        )
        result = await service.sync_character(
            character,
            scopes=(SyncScope.PROFILE, SyncScope.EQUIPMENT, SyncScope.PROFESSIONS),
        )

        assert result["status"] == SyncStatus.PARTIAL.value
        assert "professions" in result["failed"]
        assert "equipment" in result["synced"]

    async def test_scope_selection_is_respected(self, session, character):
        service = build_service(
            session, routing_handler({"/collections/mounts": {"mounts": []}})
        )
        result = await service.sync_character(
            character, scopes=(SyncScope.MOUNTS,)
        )
        assert result["synced"] == ["mounts"]

    async def test_missing_character_raises(self, session, character):
        service = build_service(session, lambda r: httpx.Response(404))
        with pytest.raises(NotFoundError, match="could not be found"):
            await service.sync_character(character)

    async def test_partial_sync_preserves_other_domains(
        self, session, character
    ):
        """A mounts-only sync must not wipe previously imported equipment."""
        first = build_service(
            session,
            routing_handler({"/equipment": {"equipped_items": [
                {"item": {"id": 1}, "name": "Axe", "slot": {"type": "MAIN_HAND"}}
            ]}}),
        )
        await first.sync_character(character, scopes=(SyncScope.EQUIPMENT,))

        second = build_service(
            session,
            routing_handler({"/collections/mounts": {"mounts": [
                {"mount": {"id": 9, "name": "Drake"}}
            ]}}),
        )
        await second.sync_character(character, scopes=(SyncScope.MOUNTS,))

        snapshot = await second.snapshots.get_for_character(character.id)
        assert snapshot.equipment["count"] == 1
        assert snapshot.mounts["total"] == 1


class TestAccountSync:
    async def test_records_a_successful_job(self, session, account):
        routes = {
            "/profile/user/wow": ACCOUNT_PROFILE,
            "/character/argent-dawn/grommash": CHARACTER_SUMMARY,
            "/equipment": {"equipped_items": []},
            "/specializations": {},
            "/professions": {},
            "/achievements": {},
            "/reputations": {},
            "/collections/mounts": {"mounts": []},
            "/collections/pets": {"pets": []},
        }
        service = build_service(session, routing_handler(routes))
        job = await service.run_account_sync(account)

        assert job.status in (SyncStatus.SUCCESS, SyncStatus.PARTIAL)
        assert job.characters_synced >= 1
        assert job.finished_at is not None
        assert job.duration_seconds is not None

    async def test_rejects_concurrent_sync(self, session, account):
        service = build_service(
            session, routing_handler({"/profile/user/wow": ACCOUNT_PROFILE})
        )
        await service.jobs.create(
            account_id=account.id,
            scope=SyncScope.FULL,
            status=SyncStatus.RUNNING,
        )

        with pytest.raises(ConflictError, match="already running"):
            await service.run_account_sync(account)


class TestAccountCollections:
    async def test_imports_account_wide_collections(self, session, account):
        routes = {
            "/collections/mounts": {"mounts": [
                {"mount": {"id": 1, "name": "Invincible"}}
            ]},
            "/collections/pets": {"pets": [
                {"id": 1, "species": {"id": 2, "name": "Murky"}, "level": 25}
            ]},
        }
        service = build_service(session, routing_handler(routes))
        result = await service.sync_account_collections(account)

        assert result["mounts"]["total"] == 1
        assert result["pets"]["max_level"] == 25
