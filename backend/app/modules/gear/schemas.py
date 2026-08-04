"""Pydantic schemas for the Gear Advisor API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.modules.gear.domain import GearRarity


class GearItemIn(BaseModel):
    slot: str
    name: str
    item_id: int = 0
    item_level: int = 0
    rarity: str = GearRarity.EPIC.value
    stats: dict[str, float] = Field(default_factory=dict)
    is_crafted: bool = False
    is_trinket: bool = False
    source: str = ""


class RecommendRequest(BaseModel):
    spec_id: int = Field(..., description="Blizzard specialization id.")
    role: str = "damage"
    equipped: list[GearItemIn] = Field(default_factory=list)
    candidates: list[GearItemIn] = Field(default_factory=list)


class CompareRequest(BaseModel):
    spec_id: int
    role: str = "damage"
    slot: str
    current: GearItemIn | None = None
    candidate: GearItemIn


class UpgradeOut(BaseModel):
    slot: str
    current: str | None
    candidate: str
    delta: float
    is_upgrade: bool


class RecommendOut(BaseModel):
    spec_id: int
    role: str
    simulated_stats: dict[str, float]
    simulated_score: float
    upgrades: list[UpgradeOut]
    enchant_recommendations: list[dict[str, Any]]
    gem_recommendations: list[dict[str, Any]]
    trinket_recommendations: list[dict[str, Any]]
    crafted_recommendations: list[dict[str, Any]]
    vault_priority: list[str]
    notes: list[str]
