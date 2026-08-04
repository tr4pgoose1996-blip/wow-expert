"""Battle.net OAuth token acquisition, caching, and automatic refresh."""

from __future__ import annotations

import asyncio
import base64
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Refresh a little before true expiry so an in-flight request never races
# the token going stale.
_EXPIRY_SKEW_SECONDS = 60


@dataclass(slots=True)
class OAuthToken:
    """An access token with its absolute expiry."""

    access_token: str
    expires_at: float
    refresh_token: str | None = None
    scopes: tuple[str, ...] = ()
    sub: str | None = None

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at - _EXPIRY_SKEW_SECONDS

    @property
    def expires_in(self) -> int:
        return max(0, int(self.expires_at - time.time()))

    @classmethod
    def from_response(cls, payload: dict) -> OAuthToken:
        return cls(
            access_token=payload["access_token"],
            expires_at=time.time() + int(payload.get("expires_in", 86_399)),
            refresh_token=payload.get("refresh_token"),
            scopes=tuple(payload.get("scope", "").split()),
            sub=payload.get("sub"),
        )


def _basic_auth_header() -> str:
    raw = f"{settings.BLIZZARD_CLIENT_ID}:{settings.BLIZZARD_CLIENT_SECRET}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


class BlizzardOAuthClient:
    """Implements both OAuth flows Battle.net exposes.

    * **Client credentials** — application-level token used for all public
      Game Data and character Profile endpoints. Cached in-process and
      refreshed automatically before expiry.
    * **Authorization code** — per-user token carrying the ``wow.profile``
      scope, required for account-level endpoints such as the account-wide
      mount and pet collections.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=settings.BLIZZARD_TIMEOUT_SECONDS
        )
        self._app_token: OAuthToken | None = None
        self._lock = asyncio.Lock()

    # -- Client credentials ------------------------------------------------

    async def get_app_token(self) -> str:
        """Return a valid application access token, refreshing if needed.

        The lock ensures that a burst of concurrent callers triggers exactly
        one token request rather than one per caller.
        """
        if self._app_token is not None and not self._app_token.is_expired:
            return self._app_token.access_token

        async with self._lock:
            # Re-check: another coroutine may have refreshed while we waited.
            if self._app_token is not None and not self._app_token.is_expired:
                return self._app_token.access_token

            if not settings.blizzard_configured:
                raise ExternalServiceError(
                    "Blizzard API credentials are not configured."
                )

            payload = await self._token_request(
                {"grant_type": "client_credentials"}
            )
            self._app_token = OAuthToken.from_response(payload)
            logger.info(
                "Acquired Blizzard application token (expires in %ss)",
                self._app_token.expires_in,
            )
            return self._app_token.access_token

    def invalidate_app_token(self) -> None:
        """Drop the cached token so the next call re-authenticates.

        Called when Blizzard returns 401, which can happen if a token is
        revoked server-side before its stated expiry.
        """
        self._app_token = None

    # -- Authorization code ------------------------------------------------

    @staticmethod
    def build_authorize_url(state: str) -> str:
        """Build the Battle.net consent URL the user is redirected to."""
        query = urlencode(
            {
                "client_id": settings.BLIZZARD_CLIENT_ID or "",
                "scope": " ".join(settings.BLIZZARD_SCOPES),
                "state": state,
                "redirect_uri": settings.BLIZZARD_REDIRECT_URI,
                "response_type": "code",
            }
        )
        return f"{settings.blizzard_oauth_host}/authorize?{query}"

    @staticmethod
    def generate_state() -> str:
        """Cryptographically random CSRF state value."""
        return secrets.token_urlsafe(32)

    async def exchange_code(self, code: str) -> OAuthToken:
        """Exchange a single-use authorization code for a user token."""
        payload = await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.BLIZZARD_REDIRECT_URI,
            }
        )
        return OAuthToken.from_response(payload)

    async def refresh_user_token(self, refresh_token: str) -> OAuthToken:
        """Exchange a refresh token for a fresh user access token."""
        payload = await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        token = OAuthToken.from_response(payload)
        # Battle.net may omit refresh_token on refresh; keep the existing one
        # so the grant is not accidentally dropped.
        if token.refresh_token is None:
            token.refresh_token = refresh_token
        return token

    async def get_userinfo(self, access_token: str) -> dict:
        """Fetch the Battle.net account id and BattleTag.

        Note this endpoint lives on the OAuth host, not the regional API host.
        """
        try:
            response = await self._client.get(
                f"{settings.blizzard_oauth_host}/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Battle.net userinfo failed with %s", exc.response.status_code
            )
            raise ExternalServiceError(
                "Could not read the Battle.net account profile."
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalServiceError("Could not reach Battle.net.") from exc

    # -- Shared ------------------------------------------------------------

    async def _token_request(self, data: dict[str, str]) -> dict:
        try:
            response = await self._client.post(
                f"{settings.blizzard_oauth_host}/token",
                data=data,
                headers={
                    "Authorization": _basic_auth_header(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            logger.error(
                "Battle.net token request (%s) failed with %s: %s",
                data.get("grant_type"),
                exc.response.status_code,
                body,
            )
            if exc.response.status_code in (400, 401):
                raise ExternalServiceError(
                    "Battle.net rejected the authorization. The grant may "
                    "have expired or been revoked; please reconnect."
                ) from exc
            raise ExternalServiceError(
                "Battle.net authorization is temporarily unavailable."
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalServiceError("Could not reach Battle.net.") from exc

    async def close(self) -> None:
        await self._client.aclose()
