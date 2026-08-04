"""Per-user Battle.net token lifecycle.

Callers ask for a usable access token and never think about expiry: this
service decrypts the stored grant, refreshes it when needed, persists the
rotated tokens, and marks the link inactive when the grant is gone for good.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenCipherError, decrypt_token, encrypt_token
from app.core.exceptions import AuthenticationError, ExternalServiceError
from app.core.logging import get_logger
from app.db.models.blizzard import BlizzardAccount
from app.integrations.blizzard.oauth import BlizzardOAuthClient, OAuthToken
from app.repositories.blizzard import BlizzardAccountRepository

logger = get_logger(__name__)


class BlizzardTokenManager:
    """Supplies valid user access tokens, refreshing transparently."""

    def __init__(
        self,
        session: AsyncSession,
        oauth: BlizzardOAuthClient | None = None,
    ) -> None:
        self.session = session
        self.repo = BlizzardAccountRepository(session)
        self.oauth = oauth or BlizzardOAuthClient()

    async def store_grant(
        self, account: BlizzardAccount, token: OAuthToken
    ) -> BlizzardAccount:
        """Persist a newly issued or refreshed grant."""
        values: dict[str, object] = {
            "access_token_encrypted": encrypt_token(token.access_token),
            "token_expires_at": datetime.fromtimestamp(
                token.expires_at, tz=UTC
            ),
            "scopes": " ".join(token.scopes),
            "is_active": True,
            "sync_error": None,
        }
        if token.refresh_token:
            values["refresh_token_encrypted"] = encrypt_token(token.refresh_token)

        return await self.repo.update(account, **values)

    async def get_access_token(self, account: BlizzardAccount) -> str:
        """Return a valid access token for the account.

        Refreshes automatically when the stored token is expired or missing.

        Raises:
            AuthenticationError: when the grant is unusable and the user must
                reconnect their Battle.net account.
        """
        if not account.is_active:
            raise AuthenticationError(
                "This Battle.net link is inactive. Please reconnect."
            )

        if not account.token_is_expired and account.access_token_encrypted:
            try:
                return decrypt_token(account.access_token_encrypted)
            except TokenCipherError:
                # Key rotation invalidated the cached access token; fall
                # through and try to obtain a new one from the refresh token.
                logger.warning(
                    "Access token for account %s could not be decrypted; "
                    "attempting refresh.",
                    account.id,
                )

        return await self._refresh(account)

    async def _refresh(self, account: BlizzardAccount) -> str:
        if not account.refresh_token_encrypted:
            await self._deactivate(
                account, "No refresh token is stored for this link."
            )
            raise AuthenticationError(
                "Your Battle.net authorization has expired. Please reconnect."
            )

        try:
            refresh_token = decrypt_token(account.refresh_token_encrypted)
        except TokenCipherError as exc:
            await self._deactivate(
                account, "The stored grant could not be decrypted."
            )
            raise AuthenticationError(
                "Your Battle.net authorization could not be read. "
                "Please reconnect your account."
            ) from exc

        try:
            token = await self.oauth.refresh_user_token(refresh_token)
        except ExternalServiceError as exc:
            # A 400/401 from the token endpoint means the user revoked access
            # or changed their password: the grant will never work again.
            await self._deactivate(account, exc.message)
            raise AuthenticationError(
                "Your Battle.net authorization is no longer valid. "
                "Please reconnect your account."
            ) from exc

        await self.store_grant(account, token)
        logger.info(
            "Refreshed Battle.net token",
            extra={"account_id": str(account.id), "expires_in": token.expires_in},
        )
        return token.access_token

    async def _deactivate(self, account: BlizzardAccount, reason: str) -> None:
        await self.repo.update(
            account,
            is_active=False,
            access_token_encrypted=None,
            sync_error=reason[:500],
        )
        logger.warning(
            "Deactivated Battle.net link",
            extra={"account_id": str(account.id), "reason": reason},
        )

    async def revoke(self, account: BlizzardAccount) -> None:
        """Forget the stored grant, e.g. when the user disconnects."""
        await self.repo.update(
            account,
            is_active=False,
            access_token_encrypted=None,
            refresh_token_encrypted=None,
            token_expires_at=None,
            scopes="",
        )
