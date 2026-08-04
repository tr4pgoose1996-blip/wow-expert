"""Authentication and account management business logic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.logging import get_logger
from app.core.redis import TokenDenyList
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import PasswordChange, TokenPair, UserCreate, UserUpdate

logger = get_logger(__name__)


class AuthService:
    """Registration, login, token lifecycle, and profile updates."""

    def __init__(
        self, session: AsyncSession, deny_list: TokenDenyList | None = None
    ) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.deny_list = deny_list or TokenDenyList()

    async def register(self, payload: UserCreate) -> User:
        if await self.users.get_by_email(payload.email):
            raise ConflictError("An account with that email already exists.")
        if await self.users.get_by_username(payload.username):
            raise ConflictError("That username is already taken.")

        try:
            user = await self.users.create(
                email=payload.email.lower(),
                username=payload.username,
                hashed_password=hash_password(payload.password),
                display_name=payload.display_name or payload.username,
            )
        except IntegrityError as exc:
            # Lost a race against a concurrent signup with the same identity.
            await self.session.rollback()
            raise ConflictError("That email or username is already in use.") from exc

        logger.info("User registered", extra={"user_id": str(user.id)})
        return user

    async def authenticate(self, identifier: str, password: str) -> User:
        user = await self.users.get_by_identifier(identifier)

        # Always run a hash comparison so a missing account and a wrong
        # password take indistinguishable time.
        stored_hash = user.hashed_password if user else _DUMMY_HASH
        password_ok = verify_password(password, stored_hash)

        if user is None or not password_ok:
            raise AuthenticationError("Incorrect credentials.")
        if not user.is_active:
            raise PermissionDeniedError("This account has been deactivated.")

        return user

    def issue_tokens(self, user: User) -> TokenPair:
        claims = {"username": user.username, "role": user.role.value}
        return TokenPair(
            access_token=create_access_token(str(user.id), claims),
            refresh_token=create_refresh_token(str(user.id)),
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def login(self, identifier: str, password: str) -> tuple[User, TokenPair]:
        user = await self.authenticate(identifier, password)
        logger.info("User logged in", extra={"user_id": str(user.id)})
        return user, self.issue_tokens(user)

    async def refresh(self, refresh_token: str) -> TokenPair:
        payload = decode_token(refresh_token, expected_type="refresh")

        jti = payload.get("jti", "")
        if await self.deny_list.is_revoked(jti):
            raise AuthenticationError("This refresh token has been revoked.")

        user = await self.users.get(uuid.UUID(payload["sub"]))
        if user is None or not user.is_active:
            raise AuthenticationError("The account is no longer active.")

        # Rotate: the presented token cannot be reused after this call.
        await self._revoke(payload)
        return self.issue_tokens(user)

    async def logout(self, refresh_token: str) -> None:
        """Best-effort revocation; an already-invalid token is a no-op."""
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except AuthenticationError:
            return
        await self._revoke(payload)

    async def _revoke(self, payload: dict) -> None:
        jti = payload.get("jti")
        exp = payload.get("exp")
        if not jti or not exp:
            return
        ttl = int(exp - datetime.now(tz=UTC).timestamp())
        await self.deny_list.revoke(jti, ttl)

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def update_profile(self, user: User, payload: UserUpdate) -> User:
        values = payload.model_dump(exclude_unset=True)
        if not values:
            return user
        return await self.users.update(user, **values)

    async def change_password(self, user: User, payload: PasswordChange) -> None:
        if not verify_password(payload.current_password, user.hashed_password):
            raise AuthenticationError("The current password is incorrect.")
        if payload.current_password == payload.new_password:
            raise ConflictError("The new password must differ from the old one.")

        await self.users.update(
            user, hashed_password=hash_password(payload.new_password)
        )
        logger.info("Password changed", extra={"user_id": str(user.id)})


# Precomputed bcrypt hash of an unguessable value, used to equalise timing
# between "no such user" and "wrong password".
_DUMMY_HASH = hash_password("timing-equalisation-placeholder-value")
