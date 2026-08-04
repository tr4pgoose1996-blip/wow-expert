"""Character persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db.models.character import Character
from app.repositories.base import BaseRepository


class CharacterRepository(BaseRepository[Character]):
    model = Character

    async def list_for_owner(
        self, owner_id: uuid.UUID, *, limit: int = 20, offset: int = 0
    ) -> list[Character]:
        stmt = (
            select(Character)
            .where(Character.owner_id == owner_id)
            .order_by(Character.level.desc(), Character.name.asc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_for_owner(self, owner_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Character)
            .where(Character.owner_id == owner_id)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def get_owned(
        self, character_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Character | None:
        """Fetch a character only if it belongs to the given owner.

        Scoping ownership into the query prevents an authorisation check from
        being forgotten at the call site.
        """
        stmt = select(Character).where(
            Character.id == character_id,
            Character.owner_id == owner_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def exists_for_owner(
        self, owner_id: uuid.UUID, name: str, realm: str
    ) -> bool:
        stmt = (
            select(Character.id)
            .where(
                Character.owner_id == owner_id,
                func.lower(Character.name) == name.lower(),
                func.lower(Character.realm) == realm.lower(),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def get_by_identity(
        self, owner_id: uuid.UUID, name: str, realm: str
    ) -> Character | None:
        """Look up a character by owner, name, and realm (case-insensitive).

        Used by the Battle.net importer to decide between insert and update.
        """
        stmt = select(Character).where(
            Character.owner_id == owner_id,
            func.lower(Character.name) == name.lower(),
            func.lower(Character.realm) == realm.lower(),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
