"""Reasoning service: turns a player message into a reasoned, explained plan
and learns the player over time.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.db.models.personalization import Conversation
from app.modules.reasoning.domain import IntentKind, PlayerContext
from app.modules.reasoning.engine import GameMasterEngine
from app.modules.reasoning.schemas import PlanOut, PlayerModelIn, ProfileOut
from app.repositories.personalization import PersonalizationRepository


def _context_from_profile(profile) -> PlayerContext:  # profile: PlayerProfile | None
    if profile is None:
        return PlayerContext(user_id="")
    return PlayerContext(
        user_id=str(profile.user_id),
        spec_id=profile.favorite_spec_id,
        spec=profile.favorite_spec,
        favorite_content=list(profile.favorite_content or []),
        current_goals=list(profile.current_goals or []),
        current_farms=list(profile.current_farms or []),
        preferred_playstyle=list(profile.preferred_playstyle or []),
        confidence=profile.confidence or 0.0,
    )


class ReasoningService:
    """Orchestrates the GM engine, persistence, and learning."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PersonalizationRepository(session)
        self._engine = GameMasterEngine()

    async def reason(
        self,
        user_id: UUID,
        message: str,
        spec_id: int | None,
        conversation_id: str | None,
    ) -> tuple[PlanOut, str, IntentKind]:
        profile = await self._repo.get_profile(user_id)
        ctx = _context_from_profile(profile)
        # An explicit spec_id on the request always wins for this turn.
        if spec_id is not None:
            ctx.spec_id = spec_id

        # Continue or start a conversation thread.
        conv: Conversation | None = None
        if conversation_id:
            conv = await self._repo.get_conversation(UUID(conversation_id))
        if conv is None:
            conv = await self._repo.create_conversation(
                user_id, title=message[:80], topic="reasoning"
            )
        await self._repo.add_message(conv.id, "user", message)

        plan = self._engine.plan(message, ctx)

        # Persist the assistant's structured plan as a message for recall.
        await self._repo.add_message(
            conv.id, "assistant", plan.summary, metadata=plan.to_dict()
        )
        await self._session.commit()

        return PlanOut(**plan.to_dict()), str(conv.id), plan.intent

    async def learn(self, user_id: UUID, data: PlayerModelIn) -> ProfileOut:
        """Store learned player facts and bump confidence."""
        fields = data.model_dump(exclude_none=True)
        profile = await self._repo.upsert_profile(user_id, **fields)
        if fields:
            profile.confidence = min(1.0, (profile.confidence or 0.0) + 0.1)
            await self._session.commit()
        return _profile_out(profile)

    async def get_profile(self, user_id: UUID) -> ProfileOut:
        profile = await self._repo.get_profile(user_id)
        if profile is None:
            raise NotFoundError("No player profile yet; teach Hermes who you are.")
        return _profile_out(profile)

    async def list_conversations(self, user_id: UUID) -> list[dict]:
        convs = await self._repo.list_conversations(user_id)
        return [
            {
                "id": str(c.id),
                "title": c.title,
                "topic": c.topic,
                "updated_at": str(c.updated_at),
            }
            for c in convs
        ]


def _profile_out(profile) -> ProfileOut:
    return ProfileOut(
        user_id=str(profile.user_id),
        favorite_class=profile.favorite_class,
        favorite_spec=profile.favorite_spec,
        favorite_spec_id=profile.favorite_spec_id,
        favorite_content=list(profile.favorite_content or []),
        current_goals=list(profile.current_goals or []),
        current_farms=list(profile.current_farms or []),
        preferred_playstyle=list(profile.preferred_playstyle or []),
        confidence=profile.confidence or 0.0,
    )
