"""Tests for the resilient Blizzard API client.

Retry, backoff, caching, and error mapping are verified against a mock
transport, with sleeps patched out so the suite stays fast.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.exceptions import ExternalServiceError, NotFoundError
from app.integrations.blizzard.client import BlizzardAPIClient
from app.integrations.blizzard.constants import Namespace, api_host
from app.integrations.blizzard.oauth import BlizzardOAuthClient, OAuthToken


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Collapse backoff delays so retry tests run instantly."""

    async def _instant(*args, **kwargs):
        return None

    monkeypatch.setattr("app.integrations.blizzard.client.asyncio.sleep", _instant)


@pytest.fixture
def oauth_stub() -> BlizzardOAuthClient:
    """An OAuth client that hands out a token without any network call."""
    client = BlizzardOAuthClient(httpx.AsyncClient())
    client._app_token = OAuthToken("app-token", expires_at=9_999_999_999)
    return client


def make_client(handler, oauth_stub, cache=None) -> BlizzardAPIClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url=api_host("us"))
    return BlizzardAPIClient(
        oauth=oauth_stub, cache=cache, http_client=http, region="us"
    )


class NoOpCache:
    """Cache that never hits, so tests exercise the network path."""

    async def get(self, key):
        return None

    async def set(self, key, value, ttl=None):
        return True

    async def delete(self, *keys):
        return 0

    async def delete_prefix(self, prefix):
        return 0


class MemoryCache(NoOpCache):
    def __init__(self):
        self.store = {}
        self.writes = 0

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl=None):
        self.store[key] = value
        self.writes += 1
        return True


class TestNamespaces:
    def test_region_suffix(self):
        assert Namespace.PROFILE.for_region("eu") == "profile-eu"
        assert Namespace.STATIC.for_region("us") == "static-us"

    def test_api_host_regional(self):
        assert api_host("eu") == "https://eu.api.blizzard.com"

    def test_api_host_china_is_special(self):
        assert api_host("cn") == "https://gateway.battlenet.com.cn"


class TestRequests:
    async def test_successful_get(self, oauth_stub):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer app-token"
            assert "namespace=profile-us" in str(request.url)
            return httpx.Response(200, json={"name": "Grommash"})

        client = make_client(handler, oauth_stub, NoOpCache())
        result = await client.get("/profile/wow/character/x/y")
        assert result == {"name": "Grommash"}
        await client._client.aclose()

    async def test_404_returns_none(self, oauth_stub):
        client = make_client(
            lambda r: httpx.Response(404, json={"code": 404}),
            oauth_stub,
            NoOpCache(),
        )
        assert await client.get("/missing") is None
        await client._client.aclose()

    async def test_get_or_404_raises(self, oauth_stub):
        client = make_client(
            lambda r: httpx.Response(404), oauth_stub, NoOpCache()
        )
        with pytest.raises(NotFoundError, match="Character"):
            await client.get_or_404("/missing", resource="Character")
        await client._client.aclose()

    async def test_403_maps_to_scope_message(self, oauth_stub):
        client = make_client(
            lambda r: httpx.Response(403), oauth_stub, NoOpCache()
        )
        with pytest.raises(ExternalServiceError, match="scope"):
            await client.get("/forbidden")
        await client._client.aclose()

    async def test_user_token_overrides_app_token(self, oauth_stub):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers["Authorization"]
            return httpx.Response(200, json={})

        client = make_client(handler, oauth_stub, NoOpCache())
        await client.get("/profile/user/wow", access_token="user-token")
        assert seen["auth"] == "Bearer user-token"
        await client._client.aclose()


class TestRetries:
    async def test_retries_500_then_succeeds(self, oauth_stub):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(500)
            return httpx.Response(200, json={"ok": True})

        client = make_client(handler, oauth_stub, NoOpCache())
        assert await client.get("/flaky") == {"ok": True}
        assert calls["n"] == 3
        await client._client.aclose()

    async def test_retries_429(self, oauth_stub):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "1"})
            return httpx.Response(200, json={"ok": True})

        client = make_client(handler, oauth_stub, NoOpCache())
        assert await client.get("/throttled") == {"ok": True}
        assert calls["n"] == 2
        await client._client.aclose()

    async def test_gives_up_after_max_retries(self, oauth_stub):
        client = make_client(
            lambda r: httpx.Response(503), oauth_stub, NoOpCache()
        )
        with pytest.raises(ExternalServiceError, match="unavailable"):
            await client.get("/always-down")
        await client._client.aclose()

    async def test_timeout_is_retried(self, oauth_stub):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.TimeoutException("too slow", request=request)
            return httpx.Response(200, json={"recovered": True})

        client = make_client(handler, oauth_stub, NoOpCache())
        assert await client.get("/slow") == {"recovered": True}
        await client._client.aclose()

    async def test_401_triggers_single_reauth(self, oauth_stub):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(401)

        client = make_client(handler, oauth_stub, NoOpCache())

        # Re-auth would normally fetch a new token; the stub keeps returning
        # one, so the client must stop after a single retry rather than loop.
        with pytest.raises(ExternalServiceError):
            await client.get("/unauthorized")
        assert calls["n"] <= 3
        await client._client.aclose()

    async def test_expired_user_token_is_not_retried(self, oauth_stub):
        """A user-token 401 must surface immediately for reconnection."""
        client = make_client(
            lambda r: httpx.Response(401), oauth_stub, NoOpCache()
        )
        with pytest.raises(ExternalServiceError, match="reconnect"):
            await client.get("/profile/user/wow", access_token="dead")
        await client._client.aclose()


class TestCaching:
    async def test_second_call_served_from_cache(self, oauth_stub):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"cached": True})

        cache = MemoryCache()
        client = make_client(handler, oauth_stub, cache)

        first = await client.get("/data", cache_ttl=300)
        second = await client.get("/data", cache_ttl=300)

        assert first == second == {"cached": True}
        assert calls["n"] == 1
        await client._client.aclose()

    async def test_user_scoped_responses_are_never_cached(self, oauth_stub):
        """Caching a user-scoped body under a shared key would leak data."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"private": True})

        cache = MemoryCache()
        client = make_client(handler, oauth_stub, cache)

        await client.get("/profile/user/wow", access_token="a", cache_ttl=300)
        await client.get("/profile/user/wow", access_token="b", cache_ttl=300)

        assert calls["n"] == 2
        assert cache.writes == 0
        await client._client.aclose()

    async def test_404_is_cached_negatively(self, oauth_stub):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        cache = MemoryCache()
        client = make_client(handler, oauth_stub, cache)

        assert await client.get("/nope", cache_ttl=300) is None
        assert await client.get("/nope", cache_ttl=300) is None
        assert calls["n"] == 1
        await client._client.aclose()

    async def test_different_params_use_different_keys(self, oauth_stub):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"n": calls["n"]})

        cache = MemoryCache()
        client = make_client(handler, oauth_stub, cache)

        await client.get("/search", params={"q": "a"}, cache_ttl=300)
        await client.get("/search", params={"q": "b"}, cache_ttl=300)
        assert calls["n"] == 2
        await client._client.aclose()


class TestBatch:
    async def test_get_many_runs_concurrently(self, oauth_stub):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"path": request.url.path})

        client = make_client(handler, oauth_stub, NoOpCache())
        results = await client.get_many(
            [{"path": "/a"}, {"path": "/b"}, {"path": "/c"}]
        )
        assert [r["path"] for r in results] == ["/a", "/b", "/c"]
        await client._client.aclose()

    async def test_one_failure_does_not_abort_batch(self, oauth_stub):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/bad":
                return httpx.Response(503)
            return httpx.Response(200, json={"ok": True})

        client = make_client(handler, oauth_stub, NoOpCache())
        results = await client.get_many([{"path": "/good"}, {"path": "/bad"}])

        assert results[0] == {"ok": True}
        assert results[1] is None
        await client._client.aclose()
