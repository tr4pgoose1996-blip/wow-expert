"""Shared FastAPI dependencies."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.redis import TokenDenyList
from app.core.security import decode_token
from app.db.models.user import User
from app.db.session import get_db_session
from app.repositories.user import UserRepository
from app.schemas.common import PaginationParams

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> User:
    """Resolve the authenticated user from the bearer access token."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("An access token is required.")

    payload = decode_token(credentials.credentials, expected_type="access")

    jti = payload.get("jti", "")
    if jti and await TokenDenyList().is_revoked(jti):
        raise AuthenticationError("This token has been revoked.")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("The token subject is malformed.") from exc

    user = await UserRepository(session).get(user_id)
    if user is None:
        raise AuthenticationError("The account no longer exists.")
    if not user.is_active:
        raise PermissionDeniedError("This account has been deactivated.")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_admin(current_user: CurrentUser) -> User:
    """Require an administrator account."""
    if not current_user.is_admin:
        raise PermissionDeniedError("Administrator privileges are required.")
    return current_user


CurrentAdmin = Annotated[User, Depends(get_current_admin)]


def get_pagination(
    limit: int = 20,
    offset: int = 0,
) -> PaginationParams:
    """Validated limit/offset query parameters."""
    return PaginationParams(limit=limit, offset=offset)


Pagination = Annotated[PaginationParams, Depends(get_pagination)]
