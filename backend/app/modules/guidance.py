"""Guidance module: character-aware AI coaching."""

from __future__ import annotations

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_session
from app.core.logging import get_logger
from app.core.redis import CacheService
from app.db.models.character import Character
from app.modules.registry import FeatureModule, registry
from app.services.ai_provider import AIProvider, ChatMessage, get_ai_provider
from app.services.character import CharacterService

logger = get_logger(__name__)

SYSTEM_PROMPT = (
    "You are wow! expert., a veteran World of Warcraft Game Master and coach. "
    "Give specific, actionable, current-expansion advice. Prefer concrete "
    "ability names, rotations, routes, and item sources over generalities. "
    "If a question falls outside World of Warcraft, say so briefly and "
    "redirect. Never invent items, spells, or encounters that do not exist; "
    "if unsure, say you are unsure."
)


class GuidanceRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    character_id: uuid.UUID | None = Field(
        default=None,
        description="Optional character to tailor the answer to.",
    )


class GuidanceResponse(BaseModel):
    answer: str
    model: str
    character_id: uuid.UUID | None = None
    cached: bool = False
    tokens_used: int = 0


def _character_context(character: Character) -> str:
    parts = [
        f"level {character.level} {character.faction.value} "
        f"{character.character_class.value.replace('_', ' ')}",
        f"playing {character.primary_role.value}",
    ]
    if character.specialization:
        parts.append(f"specialised as {character.specialization}")
    if character.item_level:
        parts.append(f"at {character.item_level} item level")
    parts.append(f"focused on {character.content_focus.value.replace('_', ' ')}")
    context = (
        f"The player's character {character.name}-{character.realm} is a "
        + ", ".join(parts)
        + "."
    )
    if character.goals:
        context += f" Their stated goals: {character.goals}"
    return context


router = APIRouter()


@router.post(
    "/ask",
    response_model=GuidanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Ask the AI Game Master a question",
    description=(
        "Answers a World of Warcraft question. When `character_id` is "
        "supplied, the character's class, role, gear, and goals are used to "
        "tailor the response. Identical question/character pairs are served "
        "from cache for ten minutes."
    ),
)
async def ask(
    payload: GuidanceRequest,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    provider: Annotated[AIProvider, Depends(get_ai_provider)],
) -> GuidanceResponse:
    character: Character | None = None
    if payload.character_id is not None:
        # Ownership is enforced here; a foreign id yields 404, not another
        # user's character context.
        character = await CharacterService(session).get_owned(
            payload.character_id, current_user.id
        )

    cache = CacheService()
    fingerprint = hashlib.sha256(
        f"{payload.question.strip().lower()}|{payload.character_id}".encode()
    ).hexdigest()[:32]
    cache_key = f"guidance:{current_user.id}:{fingerprint}"

    if (cached := await cache.get(cache_key)) is not None:
        return GuidanceResponse(**cached, cached=True)

    messages = [ChatMessage(role="system", content=SYSTEM_PROMPT)]
    if character is not None:
        messages.append(
            ChatMessage(role="system", content=_character_context(character))
        )
    messages.append(ChatMessage(role="user", content=payload.question))

    completion = await provider.complete(messages)

    result = {
        "answer": completion.content,
        "model": completion.model,
        "character_id": payload.character_id,
        "tokens_used": completion.tokens_used,
    }
    await cache.set(cache_key, result, ttl=600)

    logger.info(
        "Guidance answered",
        extra={"user_id": str(current_user.id), "tokens": completion.tokens_used},
    )
    return GuidanceResponse(**result, cached=False)


class GuidanceModule(FeatureModule):
    """Registers the guidance endpoints with the application."""

    name = "guidance"
    description = "Character-aware AI coaching and Game Master answers."

    @property
    def router(self) -> APIRouter:
        return router

    async def health(self) -> dict[str, str]:
        provider = get_ai_provider()
        return {"status": "ok", "provider": type(provider).__name__}


guidance_module = registry.register(GuidanceModule())
