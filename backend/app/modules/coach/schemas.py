"""Pydantic schemas for the Combat Coach API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.modules.coach.domain import Affix, ContentType, Difficulty, Role


class InstanceOut(BaseModel):
    journal_id: int
    name: str
    content_type: str
    boss_count: int = 0
    notable_affixes: list[str] = Field(default_factory=list)


class BossOut(BaseModel):
    journal_id: int
    name: str
    instance_id: int
    summary: str = ""
    mechanic_count: int = 0
    common_mistakes: list[str] = Field(default_factory=list)


class CoachBriefingRequest(BaseModel):
    """What role/difficulty/affixes to coach for."""

    role: Role
    difficulty: Difficulty = Difficulty.HEROIC
    affixes: list[Affix] = Field(default_factory=list)


class CoachPointOut(BaseModel):
    category: str
    text: str
    severity: str


class CoachBriefingOut(BaseModel):
    instance_name: str
    boss_name: str
    role: str
    difficulty: str
    affixes: list[str] = Field(default_factory=list)
    summary: str = ""
    briefing: list[CoachPointOut] = Field(default_factory=list)
    common_mistakes: list[str] = Field(default_factory=list)

    @classmethod
    def from_briefing(cls, b: Any) -> CoachBriefingOut:
        return cls(
            instance_name=b.instance_name,
            boss_name=b.boss_name,
            role=b.role.value,
            difficulty=b.difficulty.value,
            affixes=[a.value for a in b.affixes],
            summary=b.summary,
            briefing=[
                CoachPointOut(category=p.category, text=p.text, severity=p.severity)
                for p in b.briefing
            ],
            common_mistakes=b.common_mistakes,
        )


def _content_type_values() -> list[str]:
    return [c.value for c in ContentType]
