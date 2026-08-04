"""User persistence."""

from __future__ import annotations

from sqlalchemy import func, or_, select

from app.db.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(func.lower(User.email) == email.lower())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        stmt = select(User).where(func.lower(User.username) == username.lower())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_identifier(self, identifier: str) -> User | None:
        """Look a user up by either email or username (single query)."""
        needle = identifier.lower()
        stmt = select(User).where(
            or_(
                func.lower(User.email) == needle,
                func.lower(User.username) == needle,
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
