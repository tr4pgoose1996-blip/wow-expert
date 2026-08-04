"""Battle.net OAuth login and account linking."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.models.blizzard import BlizzardAccount
from app.integrations.blizzard.oauth import BlizzardOAuthClient
from app.repositories.blizzard import BlizzardAccountRepository
from app.services.blizzard_tokens import BlizzardTokenManager

logger = get_logger(__name__)

_STATE_PREFIX = "bnet_oauth_state:"
_STATE_TTL_SECONDS = 600


class BlizzardOAuthService:
    """Drives the authorization-code flow and links accounts to users."""

    def __init__(
        self,
        session: AsyncSession,
        oauth: BlizzardOAuthClient | None = None,
    ) -> None:
        self.session = session
        self.oauth = oauth or BlizzardOAuthClient()
        self.repo = BlizzardAccountRepository(session)
        self.tokens = BlizzardTokenManager(session, self.oauth)

    async def begin(self, user_id: uuid.UUID, region: str | None = None) -> str:
        """Create a CSRF state, stash it, and return the consent URL.

        The state is bound to the initiating user in Redis with a short TTL,
        so a callback can only ever complete for the user who started it.
        """
        if not settings.blizzard_configured:
            raise ExternalServiceError(
                "Battle.net integration is not configured on this server."
            )

        state = self.oauth.generate_state()
        payload = f"{user_id}:{region or settings.BLIZZARD_REGION}"

        await get_redis().set(
            f"{_STATE_PREFIX}{state}", payload, ex=_STATE_TTL_SECONDS
        )

        logger.info("Started Battle.net OAuth", extra={"user_id": str(user_id)})
        return self.oauth.build_authorize_url(state)

    async def complete(self, code: str, state: str) -> BlizzardAccount:
        """Consume the callback: validate state, exchange code, link account."""
        redis = get_redis()
        key = f"{_STATE_PREFIX}{state}"

        # Single-use: deleting on read prevents a replayed callback.
        stored = await redis.getdel(key)
        if stored is None:
            raise ValidationError(
                "This authorization link is invalid or has expired. "
                "Please start again."
            )

        user_id_raw, _, region = stored.partition(":")
        user_id = uuid.UUID(user_id_raw)
        region = region or settings.BLIZZARD_REGION

        token = await self.oauth.exchange_code(code)

        if "wow.profile" not in token.scopes:
            raise ValidationError(
                "The wow.profile permission is required to import "
                "characters. Please authorize it and try again."
            )

        userinfo = await self.oauth.get_userinfo(token.access_token)
        battlenet_id = int(userinfo["id"])
        battletag = userinfo.get("battletag")

        # Refuse to hijack a Battle.net account already linked elsewhere.
        existing = await self.repo.get_by_battlenet_id(battlenet_id, region)
        if existing is not None and existing.user_id != user_id:
            raise ConflictError(
                "That Battle.net account is already linked to another "
                "wow! expert. user."
            )

        account = existing or await self.repo.get_for_user(user_id, region)

        if account is None:
            account = await self.repo.create(
                user_id=user_id,
                battlenet_id=battlenet_id,
                battletag=battletag,
                region=region,
            )
        else:
            account = await self.repo.update(
                account, battlenet_id=battlenet_id, battletag=battletag
            )

        account = await self.tokens.store_grant(account, token)

        logger.info(
            "Linked Battle.net account",
            extra={"user_id": str(user_id), "battletag": battletag},
        )
        return account

    async def get_account(
        self, user_id: uuid.UUID, region: str | None = None
    ) -> BlizzardAccount:
        account = await self.repo.get_for_user(
            user_id, region or settings.BLIZZARD_REGION
        )
        if account is None:
            raise NotFoundError(
                "No Battle.net account is linked. Connect one to import "
                "your characters."
            )
        return account

    async def list_accounts(self, user_id: uuid.UUID) -> list[BlizzardAccount]:
        return await self.repo.list_for_user(user_id)

    async def disconnect(
        self, user_id: uuid.UUID, region: str | None = None
    ) -> None:
        """Unlink an account and forget its tokens."""
        account = await self.get_account(user_id, region)
        await self.tokens.revoke(account)
        await self.repo.delete(account)
        logger.info(
            "Disconnected Battle.net account", extra={"user_id": str(user_id)}
        )
