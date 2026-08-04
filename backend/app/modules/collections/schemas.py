"""Pydantic schemas for the Collection Tracker API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlanRequest(BaseModel):
    spec_id: int = Field(..., description="Blizzard specialization id.")
    categories: list[str] | None = Field(
        default=None,
        description="Optional category filter (mounts, toys, ...). Omit for all.",
    )
    owned: dict[str, list[str]] | None = Field(
        default=None,
        description="Category -> list of owned collectible ids (from Blizzard).",
    )


class CollectionGoalOut(BaseModel):
    id: str
    name: str
    category: str
    obtain: str
    minutes: float
    notes: str = ""


class CollectionPlanOut(BaseModel):
    spec_id: int
    wow_class: str | None
    categories: list[str]
    fastest_goals: list[CollectionGoalOut]
    per_category_eta_min: dict[str, float]
    total_eta_min: float
    estimated_total_min: float
    notes: list[str]


class CategorySummaryOut(BaseModel):
    category: str
    total: int
    owned: int
    remaining: int
