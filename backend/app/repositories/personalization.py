"""Data access for the player model: profile, conversations, messages."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.personalization import (
    Conversation,
    ConversationMessage,
    PlayerProfile,
)


class PersonalizationRepository:
    """Thin async CRUD over the player model tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- Profile ------------------------------------------------------------

    async def get_profile(self, user_id: UUID) -> PlayerProfile | None:
        stmt = select(PlayerProfile).where(PlayerProfile.user_id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert_profile(
        self, user_id: UUID, **fields: Any
    ) -> PlayerProfile:
        profile = await self.get_profile(user_id)
        if profile is None:
            profile = PlayerProfile(user_id=user_id)
            self._session.add(profile)
        for key, value in fields.items():
            if value is not None:
                setattr(profile, key, value)
        await self._session.flush()
        return profile

    # -- Conversations -------------------------------------------------------

    async def create_conversation(
        self, user_id: UUID, title: str | None, topic: str | None
    ) -> Conversation:
        conv = Conversation(user_id=user_id, title=title, topic=topic)
        self._session.add(conv)
        await self._session.flush()
        return conv

    async def add_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationMessage:
        msg = ConversationMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            metadata_=metadata or {},
        )
        self._session.add(msg)
        await self._session.flush()
        return msg

    async def list_conversations(self, user_id: UUID) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_conversation(self, conversation_id: UUID) -> Conversation | None:
        stmt = select(Conversation).where(Conversation.id == conversation_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()
