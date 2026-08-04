"""Game Master reasoning domain.

Hermes reasons like a veteran Game Master: it infers the player's intent,
decomposes it into ordered objectives, ranks them by an efficiency model that
combines every subsystem (gear, coach, collections, rotation, quests), and
explains *why* each step is optimal. Plans are structured data, not prose, so
they can drive the overlay and be unit-tested without an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class IntentKind(StrEnum):
    """High-level player goals the GM knows how to plan around."""

    GEAR_UP = "gear_up"
    LEARN_SPEC = "learn_spec"
    CLEAR_CONTENT = "clear_content"
    COMPLETE_COLLECTION = "complete_collection"
    IMPROVE_ROTATION = "improve_rotation"
    PROGRESS_CHARACTER = "progress_character"
    OPEN = "open"  # free-form / unknown


class ObjectiveCategory(StrEnum):
    GEAR = "gear"
    LEARN = "learn"
    PLAY = "play"  # dungeons/raids to run
    COLLECT = "collect"
    PRACTICE = "practice"  # rotation drills


@dataclass
class Objective:
    """One planned step with its rationale and supporting evidence."""

    id: str
    category: ObjectiveCategory
    title: str
    detail: str
    # Efficiency score 0..100 used to order objectives.
    priority: float
    # Plain-language explanation of why this is the right move now.
    rationale: str
    # Source subsystem that produced the evidence (gear/coach/collections/...).
    source: str
    # Estimated time to complete, in minutes (0 = instant / unknown).
    eta_minutes: float = 0.0
    # Concrete next action the player can execute immediately.
    next_action: str = ""
    # Confidence 0..1 that this objective is correct for the intent.
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    """A reasoned, ordered progression plan for a player intent."""

    intent: IntentKind
    intent_text: str
    objectives: list[Objective]
    summary: str
    confidence: float
    # Dynamic adaptations queued for when state changes (e.g., gear acquired).
    adaptations: list[str] = field(default_factory=list)
    # What the GM explicitly does NOT know yet (drives clarifying questions).
    open_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "intent_text": self.intent_text,
            "summary": self.summary,
            "confidence": round(self.confidence, 2),
            "objectives": [
                {
                    "id": o.id,
                    "category": o.category.value,
                    "title": o.title,
                    "detail": o.detail,
                    "priority": round(o.priority, 1),
                    "rationale": o.rationale,
                    "source": o.source,
                    "eta_minutes": o.eta_minutes,
                    "next_action": o.next_action,
                    "confidence": round(o.confidence, 2),
                    "metadata": o.metadata,
                }
                for o in self.objectives
            ],
            "adaptations": self.adaptations,
            "open_questions": self.open_questions,
        }


@dataclass
class PlayerContext:
    """Lightweight snapshot the engine reasons over (no ORM dependency)."""

    user_id: str
    spec_id: int | None = None
    wow_class: str | None = None
    spec: str | None = None
    role: str | None = None
    favorite_content: list[str] = field(default_factory=list)
    current_goals: list[str] = field(default_factory=list)
    current_farms: list[str] = field(default_factory=list)
    preferred_playstyle: list[str] = field(default_factory=list)
    confidence: float = 0.0
