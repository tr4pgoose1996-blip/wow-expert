"""End-to-end tests for the Battle.net HTTP endpoints.

These exercise the real routing, auth, serialization, and error handling by
driving the ASGI app, with only the outbound Blizzard calls mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import AsyncClient

from app.core.crypto import encrypt_token
from app.db.models.blizzard import BlizzardAccount
from app.integrations.blizzard.oauth import OAuthToken

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
                    "realm": {"id": 1, "slug": "argent-dawn",
                              "name": "Argent Dawn"},
                }
            ],
        }
    ]
}

REALM_SEARCH = {
    "results": [
        {
            "data": {
                "id": 1234,
                "name": {"en_US": "Argent Dawn"},
                "slug": "argent-dawn",
                "category": {"en_US": "United States"},
                "timezone": "America/New_York",
                "type": {"type": "NORMAL"},
                "is_tournament": False,
                "region": {"name": {"en_US": "North America"}},
            }
        }
    ]
}

GUILD = {
    "id": 99,
    "name": "Warsong",
    "faction": {"type": "HORDE"},
    "realm": {"name": "Argent Dawn", "slug": "argent-dawn"},
    "member_count": 120,
    "achievement_points": 3400,
}


@pytest.fixture(autouse=True)
def configure_blizzard(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "BLIZZARD_CLIENT_ID", "test-client")
    monkeypatch.setattr(settings, "BLIZZARD_CLIENT_SECRET", "test-secret")


@pytest.fixture
def mock_blizzard(monkeypatch):
    """Patch the shared API client factory to use a mock transport."""

    def install(routes: dict[str, dict]):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            for suffix, body in routes.items():
                if suffix in path:
                    return httpx.Response(200, json=body)
            return httpx.Response(404)

        from app.integrations.blizzard.client import BlizzardAPIClient
        from app.integrations.blizzard.constants import api_host
        from app.integrations.blizzard.oauth import BlizzardOAuthClient

        class NoCache:
            async def get(self, key):
                return None

            async def set(self, key, value, ttl=None):
                return True

            async def delete(self, *keys):
                return 0

            async def delete_prefix(self, prefix):
                return 0

        def factory(region=None):
            oauth = BlizzardOAuthClient(httpx.AsyncClient())
            oauth._app_token = OAuthToken("app", expires_at=9_999_999_999)
            return BlizzardAPIClient(
                oauth=oauth,
                cache=NoCache(),
                http_client=httpx.AsyncClient(
                    transport=httpx.MockTransport(handler),
                    base_url=api_host(region or "us"),
                ),
                region=region or "us",
            )

        import app.services.blizzard_search as search_module
        import app.services.blizzard_sync as sync_module

        monkeypatch.setattr(
            sync_module, "BlizzardAPIClient", lambda **kw: factory(kw.get("region"))
        )
        monkeypatch.setattr(
            search_module,
            "BlizzardAPIClient",
            lambda **kw: factory(kw.get("region")),
        )

    return install


@pytest.fixture
async def linked_account(client, auth_headers, engine) -> BlizzardAccount:
    """Attach a Battle.net account to the authenticated test user."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.db.models.user import User

    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)
    async with factory() as session:
        user = (await session.execute(select(User))).scalars().first()
        account = BlizzardAccount(
            user_id=user.id,
            battlenet_id=555,
            battletag="Thrall#1234",
            region="us",
            access_token_encrypted=encrypt_token("user-token"),
            token_expires_at=datetime.now(UTC) + timedelta(hours=5),
            scopes="openid wow.profile",
        )
        session.add(account)
        await session.commit()
        return account


class TestAuthRequired:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/api/v1/blizzard/accounts"),
            ("get", "/api/v1/blizzard/roster"),
            ("post", "/api/v1/blizzard/import"),
            ("post", "/api/v1/blizzard/sync"),
            ("get", "/api/v1/blizzard/collections"),
            ("get", "/api/v1/blizzard/oauth/authorize"),
        ],
    )
    async def test_rejects_anonymous(self, client: AsyncClient, method, path):
        kwargs = {"json": {}} if method == "post" else {}
        response = await getattr(client, method)(path, **kwargs)
        assert response.status_code == 401

    async def test_public_endpoints_need_no_auth(
        self, client: AsyncClient, mock_blizzard
    ):
        mock_blizzard({"/search/realm": REALM_SEARCH})
        response = await client.get("/api/v1/blizzard/realms?q=argent")
        assert response.status_code == 200


class TestOAuthFlow:
    async def test_authorize_returns_consent_url(
        self, client: AsyncClient, auth_headers
    ):
        response = await client.get(
            "/api/v1/blizzard/oauth/authorize", headers=auth_headers
        )
        assert response.status_code == 200

        url = response.json()["authorize_url"]
        assert url.startswith("https://oauth.battle.net/authorize")
        assert "state=" in url
        assert "wow.profile" in url

    async def test_callback_rejects_unknown_state(self, client: AsyncClient):
        response = await client.get(
            "/api/v1/blizzard/oauth/callback",
            params={"code": "abc", "state": "never-issued"},
            follow_redirects=False,
        )
        assert response.status_code in (307, 422)

    async def test_callback_redirects_when_user_declines(
        self, client: AsyncClient
    ):
        response = await client.get(
            "/api/v1/blizzard/oauth/callback",
            params={"error": "access_denied"},
            follow_redirects=False,
        )
        assert response.status_code == 307
        assert "error=access_denied" in response.headers["location"]

    async def test_list_accounts_empty(self, client: AsyncClient, auth_headers):
        response = await client.get(
            "/api/v1/blizzard/accounts", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json() == []

    async def test_list_accounts_never_exposes_tokens(
        self, client: AsyncClient, auth_headers, linked_account
    ):
        response = await client.get(
            "/api/v1/blizzard/accounts", headers=auth_headers
        )
        body = response.text.lower()

        assert response.status_code == 200
        assert response.json()[0]["battletag"] == "Thrall#1234"
        assert "access_token" not in body
        assert "user-token" not in body

    async def test_disconnect(
        self, client: AsyncClient, auth_headers, linked_account
    ):
        response = await client.delete(
            "/api/v1/blizzard/accounts", headers=auth_headers
        )
        assert response.status_code == 204

        listing = await client.get(
            "/api/v1/blizzard/accounts", headers=auth_headers
        )
        assert listing.json() == []


class TestRosterAndImport:
    async def test_roster_preview(
        self, client: AsyncClient, auth_headers, linked_account, mock_blizzard
    ):
        mock_blizzard({"/profile/user/wow": ACCOUNT_PROFILE})
        response = await client.get(
            "/api/v1/blizzard/roster", headers=auth_headers
        )

        assert response.status_code == 200
        body = response.json()
        assert body[0]["name"] == "Grommash"
        assert body[0]["character_class"] == "warrior"

    async def test_roster_without_link_is_404(
        self, client: AsyncClient, auth_headers
    ):
        response = await client.get(
            "/api/v1/blizzard/roster", headers=auth_headers
        )
        assert response.status_code == 404

    async def test_import_creates_characters(
        self, client: AsyncClient, auth_headers, linked_account, mock_blizzard
    ):
        mock_blizzard({"/profile/user/wow": ACCOUNT_PROFILE})
        response = await client.post(
            "/api/v1/blizzard/import",
            headers=auth_headers,
            json={"min_level": 10},
        )

        assert response.status_code == 200
        assert response.json()["imported"] == 1

        characters = await client.get(
            "/api/v1/characters", headers=auth_headers
        )
        names = [c["name"] for c in characters.json()["items"]]
        assert "Grommash" in names

    async def test_import_validates_min_level(
        self, client: AsyncClient, auth_headers, linked_account
    ):
        response = await client.post(
            "/api/v1/blizzard/import",
            headers=auth_headers,
            json={"min_level": 999},
        )
        assert response.status_code == 422

    async def test_snapshot_before_sync_is_404(
        self, client: AsyncClient, auth_headers, linked_account, mock_blizzard,
        character_payload
    ):
        created = await client.post(
            "/api/v1/characters", headers=auth_headers, json=character_payload
        )
        character_id = created.json()["id"]

        response = await client.get(
            f"/api/v1/blizzard/characters/{character_id}/snapshot",
            headers=auth_headers,
        )
        assert response.status_code == 404
        assert "synchronized" in response.json()["error"]["message"]

    async def test_cannot_sync_another_users_character(
        self, client: AsyncClient, auth_headers, character_payload
    ):
        created = await client.post(
            "/api/v1/characters", headers=auth_headers, json=character_payload
        )
        character_id = created.json()["id"]

        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "intruder@example.com",
                "username": "intruder",
                "password": "TotallyLegit99",
            },
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "identifier": "intruder@example.com",
                "password": "TotallyLegit99",
            },
        )
        intruder = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = await client.post(
            f"/api/v1/blizzard/characters/{character_id}/sync",
            headers=intruder,
            json={"scopes": ["full"]},
        )
        assert response.status_code == 404


class TestRealmSearch:
    async def test_search_realms(self, client: AsyncClient, mock_blizzard):
        mock_blizzard({"/search/realm": REALM_SEARCH})
        response = await client.get("/api/v1/blizzard/realms?q=argent")

        assert response.status_code == 200
        assert response.json()[0]["name"] == "Argent Dawn"
        assert response.json()[0]["slug"] == "argent-dawn"

    async def test_invalid_region_rejected(self, client: AsyncClient):
        response = await client.get("/api/v1/blizzard/realms?region=mars")
        assert response.status_code in (400, 422)

    async def test_limit_is_validated(self, client: AsyncClient):
        response = await client.get("/api/v1/blizzard/realms?limit=5000")
        assert response.status_code == 422


class TestGuildLookup:
    async def test_get_guild(self, client: AsyncClient, mock_blizzard):
        mock_blizzard({"/guild/": GUILD})
        response = await client.get(
            "/api/v1/blizzard/guilds/Argent Dawn/Warsong"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Warsong"
        assert body["faction"] == "horde"
        assert body["member_count"] == 120

    async def test_missing_guild_is_404(self, client: AsyncClient, mock_blizzard):
        mock_blizzard({})
        response = await client.get(
            "/api/v1/blizzard/guilds/argent-dawn/nonexistent"
        )
        assert response.status_code == 404

    async def test_guild_roster_paginates(
        self, client: AsyncClient, mock_blizzard
    ):
        roster = {
            "guild": GUILD,
            "members": [
                {
                    "character": {
                        "name": f"Member{i}",
                        "level": 80,
                        "realm": {"slug": "argent-dawn"},
                    },
                    "rank": i,
                }
                for i in range(10)
            ],
        }
        mock_blizzard({"/roster": roster, "/guild/": GUILD})
        response = await client.get(
            "/api/v1/blizzard/guilds/argent-dawn/warsong/roster?limit=3"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 10
        assert len(body["members"]) == 3


class TestStatus:
    async def test_reports_active(self, client: AsyncClient):
        response = await client.get("/api/v1/blizzard/status")
        assert response.status_code == 200
        assert "active" in response.json()["detail"]

    async def test_reports_unconfigured(self, client: AsyncClient, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "BLIZZARD_CLIENT_ID", None)
        response = await client.get("/api/v1/blizzard/status")
        assert "not configured" in response.json()["detail"]


class TestOpenAPI:
    async def test_blizzard_endpoints_documented(self, client: AsyncClient):
        schema = (await client.get("/openapi.json")).json()
        paths = schema["paths"]

        assert "/api/v1/blizzard/import" in paths
        assert "/api/v1/blizzard/sync" in paths
        assert "/api/v1/blizzard/realms" in paths

    async def test_endpoints_have_summaries(self, client: AsyncClient):
        schema = (await client.get("/openapi.json")).json()
        blizzard_paths = {
            p: v for p, v in schema["paths"].items() if "/blizzard/" in p
        }

        assert blizzard_paths
        for path, operations in blizzard_paths.items():
            for method, operation in operations.items():
                assert operation.get("summary"), f"{method} {path} lacks a summary"
