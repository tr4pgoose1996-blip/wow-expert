"""Quest Intelligence feature module.

Exposes the quest graph over HTTP: dependency inspection, campaign
progression, prerequisite resolution, optimisation, search, breadcrumb
detection, recommendations, and TomTom waypoint generation.

The graph is process-global and built once at startup. It is immutable and
read-only, so sharing it across requests is safe and avoids rebuilding a
large structure per call.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_session
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models.character import Character
from app.modules.registry import FeatureModule, registry
from app.navigation.router import TravelContext
from app.quests.graph import CharacterProgress, QuestGraph
from app.quests.models import QuestStatus, QuestType
from app.quests.optimizer import optimize_quest_route
from app.quests.recommender import (
    campaign_progress,
    recommend_quests,
    search_quests,
)
from app.quests.seed_data import build_seed_graph
from app.quests.waypoints import quest_waypoints, route_waypoints
from app.services.character import CharacterService
from app.world.geography import Expansion

logger = get_logger(__name__)

_graph: QuestGraph | None = None


def get_quest_graph() -> QuestGraph:
    """Return the shared quest graph, building it on first use."""
    global _graph
    if _graph is None:
        _graph = build_seed_graph()
        logger.info("Quest graph built", extra={"stats": _graph.stats()})
    return _graph


async def _progress_for(
    character_id: uuid.UUID | None,
    user_id: uuid.UUID,
    session: AsyncSession,
    *,
    completed: list[int] | None = None,
) -> CharacterProgress:
    """Build progress from a stored character, or an anonymous default.

    Explicit ``completed`` ids override the character's record so a client
    can ask hypothetical questions without mutating anything.
    """
    if character_id is None:
        return CharacterProgress(
            completed=frozenset(completed or ()),
            level=1,
        )

    character: Character = await CharacterService(session).get_owned(
        character_id, user_id
    )
    stored: list[int] = []
    if completed is None:
        raw = getattr(character, "completed_quest_ids", None)
        if isinstance(raw, list):
            stored = [int(q) for q in raw if isinstance(q, int | str) and str(q).isdigit()]

    return CharacterProgress(
        completed=frozenset(completed if completed is not None else stored),
        level=character.level,
        faction=character.faction.value,
        character_class=character.character_class.value,
        map_id=None,
    )


# --------------------------------------------------------------- schemas


class QuestOut(BaseModel):
    quest_id: int
    title: str
    type: str
    expansion: str
    campaign: str = ""
    map_id: int | None = None
    zone: str | None = None
    required_level: int
    faction: str | None = None
    estimated_seconds: float
    prerequisites: list[int] = Field(default_factory=list)


class QuestDetailOut(QuestOut):
    status: str
    missing_prerequisites: list[QuestOut] = Field(default_factory=list)
    unlocks: list[QuestOut] = Field(default_factory=list)
    waypoints: dict | None = None


class RecommendationOut(BaseModel):
    quest_id: int
    title: str
    type: str
    campaign: str
    expansion: str
    map_id: int | None
    zone: str | None
    required_level: int
    score: float
    reasons: list[str]
    travel_seconds: float
    unlocks_count: int


class RouteRequest(BaseModel):
    quest_ids: list[int] = Field(min_length=1, max_length=100)
    character_id: uuid.UUID | None = None
    origin_map_id: int | None = Field(default=None, gt=0)
    completed_quest_ids: list[int] | None = None
    include_waypoints: bool = True


class CampaignOut(BaseModel):
    campaign: str
    total: int
    completed: int
    in_progress: int
    available: int
    locked: int
    percent: float
    is_complete: bool
    next_quests: list[dict]


def _quest_out(quest, graph: QuestGraph) -> QuestOut:
    from app.world.zones import get_zone

    zone = get_zone(quest.map_id) if quest.map_id else None
    return QuestOut(
        quest_id=quest.quest_id,
        title=quest.title,
        type=quest.quest_type.value,
        expansion=quest.expansion.value,
        campaign=quest.campaign,
        map_id=quest.map_id,
        zone=zone.name if zone else None,
        required_level=quest.required_level,
        faction=quest.faction,
        estimated_seconds=quest.estimated_seconds,
        prerequisites=sorted(quest.prerequisites),
    )


router = APIRouter()


@router.get(
    "/quests/{quest_id}",
    response_model=QuestDetailOut,
    summary="Inspect a quest and its dependencies",
    description=(
        "Returns a quest with its status for the given character, the full "
        "transitive list of missing prerequisites in play order, what it "
        "unlocks, and TomTom waypoints."
    ),
)
async def get_quest(
    quest_id: int,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    graph: Annotated[QuestGraph, Depends(get_quest_graph)],
    character_id: uuid.UUID | None = None,
) -> QuestDetailOut:
    quest = graph.get(quest_id)
    if quest is None:
        raise NotFoundError(f"Quest {quest_id} is not in the index.")

    progress = await _progress_for(character_id, current_user.id, session)
    status_value = graph.status_for(quest_id, progress)

    base = _quest_out(quest, graph)
    return QuestDetailOut(
        **base.model_dump(),
        status=status_value.value,
        missing_prerequisites=[
            _quest_out(q, graph) for q in graph.missing_prerequisites(quest_id, progress)
        ],
        unlocks=[_quest_out(q, graph) for q in graph.dependents_of(quest_id)],
        waypoints=quest_waypoints(quest).as_dict(),
    )


@router.get(
    "/search",
    response_model=list[QuestOut],
    summary="Search quests by name, campaign, or NPC",
)
async def search(
    graph: Annotated[QuestGraph, Depends(get_quest_graph)],
    q: Annotated[str, Query(min_length=2, max_length=200)],
    expansion: Expansion | None = None,
    quest_type: QuestType | None = None,
    map_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[QuestOut]:
    results = search_quests(
        graph, q, limit=limit, expansion=expansion,
        quest_type=quest_type, map_id=map_id,
    )
    return [_quest_out(quest, graph) for quest in results]


@router.get(
    "/recommendations",
    response_model=list[RecommendationOut],
    summary="Recommend what to do next",
    description=(
        "Scores available quests on type, downstream unlocks, travel cost, "
        "and level fit. Obsolete breadcrumbs are excluded by default."
    ),
)
async def recommendations(
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    graph: Annotated[QuestGraph, Depends(get_quest_graph)],
    character_id: uuid.UUID | None = None,
    expansion: Expansion | None = None,
    campaign: str | None = None,
    origin_map_id: Annotated[int | None, Query(gt=0)] = None,
    include_breadcrumbs: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[RecommendationOut]:
    progress = await _progress_for(character_id, current_user.id, session)
    if origin_map_id is not None:
        progress = CharacterProgress(
            completed=progress.completed,
            in_progress=progress.in_progress,
            level=progress.level,
            faction=progress.faction,
            character_class=progress.character_class,
            race=progress.race,
            map_id=origin_map_id,
        )

    results = recommend_quests(
        graph, progress, limit=limit, expansion=expansion,
        campaign=campaign, include_breadcrumbs=include_breadcrumbs,
    )
    return [RecommendationOut(**r.as_dict()) for r in results]


@router.get(
    "/campaigns",
    response_model=list[str],
    summary="List known campaigns",
)
async def campaigns(
    graph: Annotated[QuestGraph, Depends(get_quest_graph)],
) -> list[str]:
    return graph.campaigns


@router.get(
    "/campaigns/{campaign}",
    response_model=CampaignOut,
    summary="Story progression through a campaign",
)
async def campaign_detail(
    campaign: str,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    graph: Annotated[QuestGraph, Depends(get_quest_graph)],
    character_id: uuid.UUID | None = None,
) -> CampaignOut:
    if campaign not in graph.campaigns:
        raise NotFoundError(f"Unknown campaign {campaign!r}.")
    progress = await _progress_for(character_id, current_user.id, session)
    return CampaignOut(**campaign_progress(graph, campaign, progress).as_dict())


@router.get(
    "/breadcrumbs",
    response_model=list[QuestOut],
    summary="Breadcrumb quests that are now obsolete",
    description=(
        "Breadcrumbs point at content the player has not found yet. Once "
        "that content is underway they are noise; this lists the ones to "
        "skip."
    ),
)
async def obsolete_breadcrumbs(
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    graph: Annotated[QuestGraph, Depends(get_quest_graph)],
    character_id: uuid.UUID | None = None,
) -> list[QuestOut]:
    progress = await _progress_for(character_id, current_user.id, session)
    return [
        _quest_out(quest, graph)
        for quest in graph.obsolete_breadcrumbs(progress)
    ]


@router.post(
    "/route",
    status_code=status.HTTP_200_OK,
    summary="Optimise a questing route",
    description=(
        "Orders quests to minimise travel while respecting prerequisites, "
        "and returns a pasteable TomTom macro. Legs with no modelled travel "
        "route are flagged with `travel_estimated`."
    ),
)
async def plan_route(
    payload: RouteRequest,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    graph: Annotated[QuestGraph, Depends(get_quest_graph)],
) -> dict:
    progress = await _progress_for(
        payload.character_id,
        current_user.id,
        session,
        completed=payload.completed_quest_ids,
    )

    quests = []
    unknown: list[int] = []
    for quest_id in payload.quest_ids:
        quest = graph.get(quest_id)
        if quest is None:
            unknown.append(quest_id)
        else:
            quests.append(quest)

    if not quests:
        raise NotFoundError("None of the requested quest ids are in the index.")

    context = TravelContext(
        faction=progress.faction,
        character_class=progress.character_class,
        level=progress.level,
    )
    route = optimize_quest_route(
        quests, graph, progress,
        context=context, origin_map_id=payload.origin_map_id,
    )

    result = route.as_dict()
    result["unknown_quest_ids"] = unknown
    if payload.include_waypoints:
        result["waypoints"] = route_waypoints(route).as_dict()
    return result


@router.get(
    "/stats",
    summary="Quest index statistics",
)
async def stats(
    graph: Annotated[QuestGraph, Depends(get_quest_graph)],
) -> dict:
    return {
        "graph": graph.stats(),
        "campaigns": graph.campaigns,
        "expansions": [e.value for e in Expansion],
    }


class QuestsModule(FeatureModule):
    """Registers quest intelligence endpoints."""

    name = "quests"
    description = (
        "Quest dependency graphs, campaign tracking, prerequisites, "
        "optimisation, search, breadcrumbs, and recommendations."
    )

    @property
    def router(self) -> APIRouter:
        return router

    async def startup(self) -> None:
        graph = get_quest_graph()
        problems = graph.validate()
        if problems:
            # Surfaced rather than raised: a partly-broken quest index is
            # still useful, and refusing to boot the whole app over one bad
            # data row would be the wrong trade.
            logger.error(
                "Quest graph has %d integrity problem(s)", len(problems),
                extra={"problems": problems[:10]},
            )

    async def health(self) -> dict[str, str]:
        graph = get_quest_graph()
        return {
            "status": "ok",
            "quests": str(len(graph)),
            "campaigns": str(len(graph.campaigns)),
        }


quests_module = registry.register(QuestsModule())
