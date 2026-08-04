"""Pydantic schemas for the reasoning / Game Master module."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PlayerModelIn(BaseModel):
    """Learned player facts Hermes stores to personalize future reasoning."""

    favorite_class: str | None = None
    favorite_spec: str | None = None
    favorite_spec_id: int | None = None
    favorite_content: list[str] | None = None
    current_goals: list[str] | None = None
    current_farms: list[str] | None = None
    preferred_playstyle: list[str] | None = None


class ReasonRequest(BaseModel):
    """A player message to reason about."""

    message: str = Field(min_length=2, max_length=2000)
    spec_id: int | None = Field(
        default=None, description="Override/pass the active specialization id."
    )
    # Optional conversation id to continue an existing thread.
    conversation_id: str | None = None


class ObjectiveOut(BaseModel):
    id: str
    category: str
    title: str
    detail: str
    priority: float
    rationale: str
    source: str
    eta_minutes: float
    next_action: str
    confidence: float
    metadata: dict[str, Any] = {}


class PlanOut(BaseModel):
    intent: str
    intent_text: str
    summary: str
    confidence: float
    objectives: list[ObjectiveOut]
    adaptations: list[str]
    open_questions: list[str]


class ReasonResponse(BaseModel):
    plan: PlanOut
    conversation_id: str
    intent: str


class ProfileOut(BaseModel):
    user_id: str
    favorite_class: str | None = None
    favorite_spec: str | None = None
    favorite_spec_id: int | None = None
    favorite_content: list[str] = []
    current_goals: list[str] = []
    current_farms: list[str] = []
    preferred_playstyle: list[str] = []
    confidence: float = 0.0
