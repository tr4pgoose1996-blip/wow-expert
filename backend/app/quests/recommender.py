"""Quest recommendations and search.

Recommendations answer "what should I do next?" by scoring available quests
against a character's state. Search answers "where is X?" over the graph.

The scoring model is explicit and inspectable rather than a black box: every
recommendation carries the reasons that produced it, so a player can tell
whether the advice actually fits their situation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.navigation.router import TravelContext, find_route
from app.quests.graph import CharacterProgress, QuestGraph
from app.quests.models import Quest, QuestStatus, QuestType
from app.world.geography import Expansion
from app.world.zones import get_zone

logger = get_logger(__name__)

__all__ = [
    "CampaignProgress",
    "QuestRecommendation",
    "campaign_progress",
    "recommend_quests",
    "search_quests",
]


@dataclass(frozen=True, slots=True)
class QuestRecommendation:
    """A scored suggestion with its justification."""

    quest: Quest
    score: float
    reasons: tuple[str, ...]
    travel_seconds: float = 0.0
    #: How many further quests this one unlocks.
    unlocks_count: int = 0

    def as_dict(self) -> dict:
        zone = get_zone(self.quest.map_id) if self.quest.map_id else None
        return {
            "quest_id": self.quest.quest_id,
            "title": self.quest.title,
            "type": self.quest.quest_type.value,
            "campaign": self.quest.campaign,
            "expansion": self.quest.expansion.value,
            "map_id": self.quest.map_id,
            "zone": zone.name if zone else None,
            "required_level": self.quest.required_level,
            "score": round(self.score, 3),
            "reasons": list(self.reasons),
            "travel_seconds": round(self.travel_seconds, 1),
            "unlocks_count": self.unlocks_count,
        }


# Scoring weights. Tuned so that a campaign quest in the current zone beats a
# side quest two continents away, without letting travel dominate entirely —
# players will cross a continent for story, but not for a filler quest.
_WEIGHT_TYPE = 0.30
_WEIGHT_UNLOCKS = 0.25
_WEIGHT_TRAVEL = 0.25
_WEIGHT_LEVEL = 0.20

#: Travel beyond this is treated as maximally distant; a five-minute journey
#: and a ten-minute one are equally discouraging.
_TRAVEL_SATURATION = 300.0


def recommend_quests(
    graph: QuestGraph,
    progress: CharacterProgress,
    *,
    limit: int = 10,
    expansion: Expansion | None = None,
    campaign: str | None = None,
    context: TravelContext | None = None,
    include_breadcrumbs: bool = False,
) -> list[QuestRecommendation]:
    """Rank what the character should do next.

    Obsolete breadcrumbs are excluded by default — the graph already marks
    them, and surfacing them is the classic way a quest helper wastes a
    player's time.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")

    context = context or TravelContext(
        faction=progress.faction,
        character_class=progress.character_class,
        level=progress.level,
    )

    candidates = graph.available_quests(progress, include_in_progress=False)
    if expansion is not None:
        candidates = [q for q in candidates if q.expansion is expansion]
    if campaign is not None:
        candidates = [q for q in candidates if q.campaign == campaign]
    if not include_breadcrumbs:
        candidates = [q for q in candidates if not q.is_breadcrumb]

    # Travel is the expensive signal — one graph search per distinct zone,
    # not per quest, since quests in the same zone share a cost.
    travel_cache: dict[int, float] = {}

    def travel_cost(map_id: int | None) -> float:
        if map_id is None or progress.map_id is None:
            return 0.0
        if map_id == progress.map_id:
            return 0.0
        if map_id not in travel_cache:
            route = find_route(progress.map_id, map_id, context)
            travel_cache[map_id] = (
                route.total_seconds if route else _TRAVEL_SATURATION
            )
        return travel_cache[map_id]

    recommendations: list[QuestRecommendation] = []

    for quest in candidates:
        reasons: list[str] = []

        # Type priority, normalised so 0 is best.
        max_priority = max(t.priority for t in QuestType)
        type_score = 1.0 - (quest.quest_type.priority / max_priority)
        if quest.quest_type is QuestType.CAMPAIGN:
            reasons.append("Advances the main campaign")
        elif quest.quest_type is QuestType.UNLOCK:
            reasons.append("Unlocks new content or features")
        elif quest.quest_type is QuestType.CLASS:
            reasons.append("Class storyline")

        # Downstream value: quests that open up more quests are worth more.
        dependents = graph.dependents_of(quest.quest_id)
        unlocks = len(dependents)
        unlock_score = min(1.0, unlocks / 3.0)
        if unlocks:
            reasons.append(
                f"Unlocks {unlocks} further quest{'s' if unlocks != 1 else ''}"
            )

        seconds = travel_cost(quest.map_id)
        travel_score = 1.0 - min(1.0, seconds / _TRAVEL_SATURATION)
        if seconds == 0.0 and quest.map_id == progress.map_id:
            reasons.append("In your current zone")
        elif seconds > 0:
            reasons.append(f"About {int(seconds)}s travel away")

        # Level fit: quests at or just below the character's level are ideal.
        gap = progress.level - quest.required_level
        if gap < 0:
            level_score = 0.0
        elif gap <= 5:
            level_score = 1.0
            reasons.append("Well matched to your level")
        else:
            level_score = max(0.0, 1.0 - (gap - 5) / 20.0)
            if gap > 15:
                reasons.append("Low-level content for you")

        score = (
            _WEIGHT_TYPE * type_score
            + _WEIGHT_UNLOCKS * unlock_score
            + _WEIGHT_TRAVEL * travel_score
            + _WEIGHT_LEVEL * level_score
        )

        recommendations.append(
            QuestRecommendation(
                quest=quest,
                score=score,
                reasons=tuple(reasons),
                travel_seconds=seconds,
                unlocks_count=unlocks,
            )
        )

    recommendations.sort(key=lambda r: (-r.score, r.quest.quest_id))
    return recommendations[:limit]


@dataclass(frozen=True, slots=True)
class CampaignProgress:
    """How far through a campaign a character is."""

    campaign: str
    total: int
    completed: int
    in_progress: int
    available: int
    locked: int
    next_quests: tuple[Quest, ...] = field(default_factory=tuple)

    @property
    def percent(self) -> float:
        return round(100.0 * self.completed / self.total, 1) if self.total else 0.0

    @property
    def is_complete(self) -> bool:
        return self.total > 0 and self.completed == self.total

    def as_dict(self) -> dict:
        return {
            "campaign": self.campaign,
            "total": self.total,
            "completed": self.completed,
            "in_progress": self.in_progress,
            "available": self.available,
            "locked": self.locked,
            "percent": self.percent,
            "is_complete": self.is_complete,
            "next_quests": [
                {"quest_id": q.quest_id, "title": q.title, "map_id": q.map_id}
                for q in self.next_quests
            ],
        }


def campaign_progress(
    graph: QuestGraph,
    campaign: str,
    progress: CharacterProgress,
    *,
    next_limit: int = 3,
) -> CampaignProgress:
    """Story progression through one campaign."""
    quests = graph.campaign_quests(campaign)
    counts = dict.fromkeys(
        (
            QuestStatus.COMPLETED,
            QuestStatus.IN_PROGRESS,
            QuestStatus.AVAILABLE,
            QuestStatus.LOCKED,
        ),
        0,
    )
    next_up: list[Quest] = []

    for quest in quests:
        status = graph.status_for(quest.quest_id, progress)
        if status in counts:
            counts[status] += 1
        if status is QuestStatus.AVAILABLE and len(next_up) < next_limit:
            next_up.append(quest)

    return CampaignProgress(
        campaign=campaign,
        total=len(quests),
        completed=counts[QuestStatus.COMPLETED],
        in_progress=counts[QuestStatus.IN_PROGRESS],
        available=counts[QuestStatus.AVAILABLE],
        locked=counts[QuestStatus.LOCKED],
        next_quests=tuple(next_up),
    )


def _tokenise(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9']+", text.lower()) if len(t) > 1]


def search_quests(
    graph: QuestGraph,
    query: str,
    *,
    limit: int = 20,
    expansion: Expansion | None = None,
    quest_type: QuestType | None = None,
    map_id: int | None = None,
) -> list[Quest]:
    """Find quests by title, campaign, or NPC.

    Scores exact and prefix matches above scattered token hits so that
    searching "Ragnaros" surfaces the quest named after him rather than
    every quest mentioning him in passing.
    """
    tokens = _tokenise(query)
    if not tokens:
        return []

    scored: list[tuple[float, Quest]] = []

    for quest in graph.quests:
        if expansion is not None and quest.expansion is not expansion:
            continue
        if quest_type is not None and quest.quest_type is not quest_type:
            continue
        if map_id is not None and quest.map_id != map_id:
            continue

        title_lower = quest.title.lower()
        haystack = " ".join(
            (quest.title, quest.campaign, quest.start_npc, quest.end_npc)
        ).lower()
        haystack_tokens = set(_tokenise(haystack))

        matched = sum(1 for token in tokens if token in haystack_tokens)
        if not matched:
            # Fall back to substring so partial words still match.
            matched = sum(1 for token in tokens if token in haystack)
            if not matched:
                continue

        score = matched / len(tokens)
        if title_lower == query.strip().lower():
            score += 2.0
        elif title_lower.startswith(query.strip().lower()):
            score += 1.0
        elif query.strip().lower() in title_lower:
            score += 0.5

        scored.append((score, quest))

    scored.sort(key=lambda pair: (-pair[0], pair[1].quest_id))
    return [quest for _, quest in scored[:limit]]
