"""Character profile business logic."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.redis import CacheService
from app.db.models.character import Character
from app.db.models.enums import role_is_valid_for_class
from app.repositories.character import CharacterRepository
from app.schemas.character import CharacterCreate, CharacterUpdate

logger = get_logger(__name__)

MAX_CHARACTERS_PER_USER = 50
_CACHE_TTL = 120


class CharacterService:
    """Owns character CRUD, ownership enforcement, and cache invalidation."""

    def __init__(
        self, session: AsyncSession, cache: CacheService | None = None
    ) -> None:
        self.session = session
        self.repo = CharacterRepository(session)
        self.cache = cache or CacheService()

    @staticmethod
    def _list_prefix(owner_id: uuid.UUID) -> str:
        return f"characters:{owner_id}:"

    async def create(
        self, owner_id: uuid.UUID, payload: CharacterCreate
    ) -> Character:
        if await self.repo.count_for_owner(owner_id) >= MAX_CHARACTERS_PER_USER:
            raise ConflictError(
                f"You may store at most {MAX_CHARACTERS_PER_USER} characters."
            )
        if await self.repo.exists_for_owner(owner_id, payload.name, payload.realm):
            raise ConflictError(
                f"{payload.name} on {payload.realm} is already in your roster."
            )

        try:
            character = await self.repo.create(
                owner_id=owner_id, **payload.model_dump()
            )
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("That character already exists.") from exc

        await self.cache.delete_prefix(self._list_prefix(owner_id))
        logger.info(
            "Character created",
            extra={"character_id": str(character.id), "owner_id": str(owner_id)},
        )
        return character

    async def get_owned(
        self, character_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Character:
        character = await self.repo.get_owned(character_id, owner_id)
        if character is None:
            # Do not distinguish "missing" from "someone else's": that would
            # leak the existence of other users' characters.
            raise NotFoundError("Character not found.")
        return character

    async def list_for_owner(
        self, owner_id: uuid.UUID, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[Character], int]:
        characters = await self.repo.list_for_owner(
            owner_id, limit=limit, offset=offset
        )
        total = await self.repo.count_for_owner(owner_id)
        return characters, total

    async def update(
        self,
        character_id: uuid.UUID,
        owner_id: uuid.UUID,
        payload: CharacterUpdate,
    ) -> Character:
        character = await self.get_owned(character_id, owner_id)
        values = payload.model_dump(exclude_unset=True)
        if not values:
            return character

        # Revalidate the class/role pairing against the merged end state,
        # not just the fields present in this request.
        new_class = values.get("character_class", character.character_class)
        new_role = values.get("primary_role", character.primary_role)
        if not role_is_valid_for_class(new_class, new_role):
            raise ValidationError(
                f"A {new_class.value} cannot fill the {new_role.value} role."
            )

        updated = await self.repo.update(character, **values)
        await self.cache.delete_prefix(self._list_prefix(owner_id))
        return updated

    async def delete(self, character_id: uuid.UUID, owner_id: uuid.UUID) -> None:
        character = await self.get_owned(character_id, owner_id)
        await self.repo.delete(character)
        await self.cache.delete_prefix(self._list_prefix(owner_id))
        logger.info("Character deleted", extra={"character_id": str(character_id)})
