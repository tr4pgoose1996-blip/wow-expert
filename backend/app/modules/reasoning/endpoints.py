"""Reasoning / Game Master endpoints.

Hermes reasons like a veteran GM: it infers intent, plans objectives across
every subsystem, and explains *why* each step is optimal. Player facts are
persisted so future reasoning is personalized.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_session
from app.core.logging import get_logger
from app.modules.reasoning.schemas import (
    PlayerModelIn,
    ProfileOut,
    ReasonRequest,
    ReasonResponse,
)
from app.modules.reasoning.service import ReasoningService

logger = get_logger(__name__)

router = APIRouter(tags=["reasoning"])


@router.post(
    "/reason",
    response_model=ReasonResponse,
    status_code=status.HTTP_200_OK,
    summary="Reason about a player goal like a Game Master",
)
async def reason(
    payload: ReasonRequest,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReasonResponse:
    """Decompose a player's intent into an ordered, explained progression plan."""
    svc = ReasoningService(session)
    plan, conv_id, _intent = await svc.reason(
        current_user.id, payload.message, payload.spec_id, payload.conversation_id
    )
    return ReasonResponse(plan=plan, conversation_id=conv_id, intent=_intent.value)


@router.put(
    "/profile",
    response_model=ProfileOut,
    status_code=status.HTTP_200_OK,
    summary="Teach Hermes about the player",
)
async def learn(
    payload: PlayerModelIn,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileOut:
    """Persist learned player facts (favorite class/spec, goals, playstyle)."""
    svc = ReasoningService(session)
    return await svc.learn(current_user.id, payload)


@router.get(
    "/profile",
    response_model=ProfileOut,
    status_code=status.HTTP_200_OK,
    summary="Recall what Hermes knows about the player",
)
async def get_profile(
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileOut:
    svc = ReasoningService(session)
    return await svc.get_profile(current_user.id)


@router.get(
    "/conversations",
    status_code=status.HTTP_200_OK,
    summary="List remembered conversations",
)
async def conversations(
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    svc = ReasoningService(session)
    return await svc.list_conversations(current_user.id)
