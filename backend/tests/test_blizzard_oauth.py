"""Tests for the Blizzard OAuth client and automatic token refresh."""

from __future__ import annotations

import time
from datetime import UTC

import httpx
import pytest

from app.core.crypto import decrypt_token, encrypt_token
from app.core.exceptions import AuthenticationError, ExternalServiceError
from app.db.models.blizzard import BlizzardAccount
from app.db.models.user import User
from app.integrations.blizzard.oauth import BlizzardOAuthClient, OAuthToken
from app.services.blizzard_tokens import BlizzardTokenManager


def _mock_oauth(handler) -> BlizzardOAuthClient:
    transport = httpx.MockTransport(handler)
    return BlizzardOAuthClient(httpx.AsyncClient(transport=transport))


class TestOAuthToken:
    def test_fresh_token_not_expired(self):
        token = OAuthToken("abc", expires_at=time.time() + 3600)
        assert not token.is_expired
        assert token.expires_in > 3500

    def test_expired_token(self):
        token = OAuthToken("abc", expires_at=time.time() - 1)
        assert token.is_expired
        assert token.expires_in == 0

    def test_skew_treats_near_expiry_as_expired(self):
        # 30s remaining is inside the 60s safety skew.
        token = OAuthToken("abc", expires_at=time.time() + 30)
        assert token.is_expired

    def test_from_response(self):
        token = OAuthToken.from_response(
            {
                "access_token": "xyz",
                "expires_in": 86399,
                "scope": "openid wow.profile",
                "refresh_token": "refresh-me",
            }
        )
        assert token.access_token == "xyz"
        assert token.scopes == ("openid", "wow.profile")
        assert token.refresh_token == "refresh-me"
        assert not token.is_expired


class TestAuthorizeUrl:
    def test_contains_required_parameters(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "BLIZZARD_CLIENT_ID", "test-client")
        url = BlizzardOAuthClient.build_authorize_url("state-123")

        assert url.startswith("https://oauth.battle.net/authorize?")
        assert "client_id=test-client" in url
        assert "response_type=code" in url
        assert "state=state-123" in url
        assert "wow.profile" in url

    def test_state_is_random_and_long(self):
        first = BlizzardOAuthClient.generate_state()
        second = BlizzardOAuthClient.generate_state()
        assert first != second
        assert len(first) >= 32


class TestClientCredentials:
    @pytest.fixture(autouse=True)
    def _configure(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "BLIZZARD_CLIENT_ID", "id")
        monkeypatch.setattr(settings, "BLIZZARD_CLIENT_SECRET", "secret")

    async def test_acquires_and_caches_token(self):
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(
                200, json={"access_token": "app-token", "expires_in": 86399}
            )

        client = _mock_oauth(handler)

        assert await client.get_app_token() == "app-token"
        assert await client.get_app_token() == "app-token"
        # Cached: only one network round-trip.
        assert calls["count"] == 1
        await client.close()

    async def test_concurrent_callers_share_one_request(self):
        import asyncio

        calls = {"count": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            await asyncio.sleep(0.01)
            return httpx.Response(
                200, json={"access_token": "t", "expires_in": 86399}
            )

        client = BlizzardOAuthClient(
            httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

        results = await asyncio.gather(*(client.get_app_token() for _ in range(10)))
        assert all(r == "t" for r in results)
        # The lock collapses the burst into a single token request.
        assert calls["count"] == 1
        await client.close()

    async def test_invalidate_forces_refetch(self):
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(
                200, json={"access_token": "t", "expires_in": 86399}
            )

        client = _mock_oauth(handler)
        await client.get_app_token()
        client.invalidate_app_token()
        await client.get_app_token()
        assert calls["count"] == 2
        await client.close()

    async def test_rejects_when_unconfigured(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "BLIZZARD_CLIENT_ID", None)
        client = _mock_oauth(lambda r: httpx.Response(200, json={}))

        with pytest.raises(ExternalServiceError, match="not configured"):
            await client.get_app_token()
        await client.close()

    async def test_401_raises_service_error(self):
        client = _mock_oauth(
            lambda r: httpx.Response(401, json={"error": "invalid_client"})
        )
        with pytest.raises(ExternalServiceError):
            await client.get_app_token()
        await client.close()


class TestAuthorizationCode:
    @pytest.fixture(autouse=True)
    def _configure(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "BLIZZARD_CLIENT_ID", "id")
        monkeypatch.setattr(settings, "BLIZZARD_CLIENT_SECRET", "secret")

    async def test_exchange_code(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode()
            assert "grant_type=authorization_code" in body
            assert "code=the-code" in body
            return httpx.Response(
                200,
                json={
                    "access_token": "user-token",
                    "refresh_token": "user-refresh",
                    "expires_in": 86399,
                    "scope": "openid wow.profile",
                },
            )

        client = _mock_oauth(handler)
        token = await client.exchange_code("the-code")
        assert token.access_token == "user-token"
        assert "wow.profile" in token.scopes
        await client.close()

    async def test_refresh_preserves_refresh_token_when_omitted(self):
        """Blizzard may omit refresh_token on refresh; it must be retained."""
        client = _mock_oauth(
            lambda r: httpx.Response(
                200, json={"access_token": "new-access", "expires_in": 86399}
            )
        )
        token = await client.refresh_user_token("original-refresh")
        assert token.access_token == "new-access"
        assert token.refresh_token == "original-refresh"
        await client.close()

    async def test_revoked_grant_surfaces_clear_message(self):
        client = _mock_oauth(
            lambda r: httpx.Response(400, json={"error": "invalid_grant"})
        )
        with pytest.raises(ExternalServiceError, match="reconnect"):
            await client.refresh_user_token("dead-token")
        await client.close()

    async def test_userinfo(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer tok"
            assert "oauth.battle.net/userinfo" in str(request.url)
            return httpx.Response(
                200, json={"id": 987654, "battletag": "Thrall#1234"}
            )

        client = _mock_oauth(handler)
        info = await client.get_userinfo("tok")
        assert info["battletag"] == "Thrall#1234"
        await client.close()


class TestTokenEncryption:
    def test_round_trip(self):
        secret = "a-refresh-token-value"
        assert decrypt_token(encrypt_token(secret)) == secret

    def test_ciphertext_differs_from_plaintext(self):
        assert encrypt_token("secret") != "secret"

    def test_two_encryptions_differ(self):
        # Fernet includes a random IV, so identical input yields distinct output.
        assert encrypt_token("same") != encrypt_token("same")


class TestTokenManager:
    async def _account(self, session, **overrides) -> BlizzardAccount:
        user = User(
            email="t@example.com",
            username="tester",
            hashed_password="x",
        )
        session.add(user)
        await session.flush()

        account = BlizzardAccount(
            user_id=user.id,
            battlenet_id=1,
            battletag="T#1",
            region="us",
            **overrides,
        )
        session.add(account)
        await session.flush()
        return account

    async def test_returns_cached_token_when_valid(self, session):
        from datetime import datetime, timedelta

        account = await self._account(
            session,
            access_token_encrypted=encrypt_token("still-good"),
            token_expires_at=datetime.now(UTC) + timedelta(hours=5),
        )
        manager = BlizzardTokenManager(session)
        assert await manager.get_access_token(account) == "still-good"

    async def test_refreshes_expired_token(self, session, monkeypatch):
        from datetime import datetime, timedelta

        from app.core.config import settings

        monkeypatch.setattr(settings, "BLIZZARD_CLIENT_ID", "id")
        monkeypatch.setattr(settings, "BLIZZARD_CLIENT_SECRET", "secret")

        account = await self._account(
            session,
            access_token_encrypted=encrypt_token("stale"),
            refresh_token_encrypted=encrypt_token("refresh-me"),
            token_expires_at=datetime.now(UTC) - timedelta(hours=1),
        )

        oauth = _mock_oauth(
            lambda r: httpx.Response(
                200,
                json={
                    "access_token": "freshly-minted",
                    "expires_in": 86399,
                    "scope": "wow.profile",
                },
            )
        )
        manager = BlizzardTokenManager(session, oauth)

        assert await manager.get_access_token(account) == "freshly-minted"
        # The new token is persisted, not just returned.
        assert decrypt_token(account.access_token_encrypted) == "freshly-minted"
        await oauth.close()

    async def test_missing_refresh_token_deactivates(self, session):
        account = await self._account(session)
        manager = BlizzardTokenManager(session)

        with pytest.raises(AuthenticationError, match="reconnect"):
            await manager.get_access_token(account)
        assert account.is_active is False

    async def test_revoked_grant_deactivates_account(self, session, monkeypatch):
        from datetime import datetime, timedelta

        from app.core.config import settings

        monkeypatch.setattr(settings, "BLIZZARD_CLIENT_ID", "id")
        monkeypatch.setattr(settings, "BLIZZARD_CLIENT_SECRET", "secret")

        account = await self._account(
            session,
            refresh_token_encrypted=encrypt_token("revoked"),
            token_expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        oauth = _mock_oauth(
            lambda r: httpx.Response(400, json={"error": "invalid_grant"})
        )
        manager = BlizzardTokenManager(session, oauth)

        with pytest.raises(AuthenticationError):
            await manager.get_access_token(account)
        assert account.is_active is False
        assert account.sync_error is not None
        await oauth.close()

    async def test_inactive_account_rejected(self, session):
        account = await self._account(session, is_active=False)
        manager = BlizzardTokenManager(session)

        with pytest.raises(AuthenticationError, match="inactive"):
            await manager.get_access_token(account)

    async def test_revoke_clears_tokens(self, session):
        account = await self._account(
            session,
            access_token_encrypted=encrypt_token("a"),
            refresh_token_encrypted=encrypt_token("b"),
        )
        await BlizzardTokenManager(session).revoke(account)

        assert account.access_token_encrypted is None
        assert account.refresh_token_encrypted is None
        assert account.is_active is False
