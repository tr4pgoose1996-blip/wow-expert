"""Hermes Navigation AI feature module.

Travel planning across Azeroth: portals, mage teleports, Dreamwalk,
engineering wormholes, flight paths, and boats — filtered by what the
character can actually use, with TomTom output.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_session
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.modules.registry import FeatureModule, registry
from app.navigation.router import TravelContext, find_route, find_routes
from app.navigation.tomtom import Waypoint, WaypointSet, format_way_command
from app.navigation.travel import TravelMethod, edges_from, validate_network
from app.services.character import CharacterService
from app.world.geography import MapPoint
from app.world.zones import ZONES, get_zone

logger = get_logger(__name__)

router = APIRouter()


# --------------------------------------------------------------- schemas


class ZoneOut(BaseModel):
    map_id: int
    name: str
    expansion: str
    continent: str
    level_range: tuple[int, int] | None = None
    faction: str | None = None
    is_city: bool
    flyable: bool


class RouteRequest(BaseModel):
    origin_map_id: int = Field(gt=0)
    destination_map_id: int = Field(gt=0)
    character_id: uuid.UUID | None = None
    faction: str | None = None
    character_class: str | None = None
    level: int | None = Field(default=None, ge=1, le=90)
    professions: dict[str, int] = Field(default_factory=dict)
    hearthstone_map_id: int | None = Field(default=None, gt=0)
    exclude_methods: list[TravelMethod] = Field(default_factory=list)
    #: Number of distinct alternatives to return.
    alternatives: int = Field(default=1, ge=1, le=5)


class WaypointRequest(BaseModel):
    """Ad-hoc waypoint generation."""

    class Point(BaseModel):
        map_id: int = Field(gt=0)
        x: float = Field(ge=0, le=100)
        y: float = Field(ge=0, le=100)
        description: str = Field(default="", max_length=200)

    points: list[Point] = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=200)
    include_reset: bool = True


async def _context_from(
    payload: RouteRequest, user_id: uuid.UUID, session: AsyncSession
) -> TravelContext:
    """Build a travel context, preferring a stored character when given."""
    faction = payload.faction
    character_class = payload.character_class
    level = payload.level
    professions = dict(payload.professions)

    if payload.character_id is not None:
        character = await CharacterService(session).get_owned(
            payload.character_id, user_id
        )
        faction = faction or character.faction.value
        character_class = character_class or character.character_class.value
        level = level or character.level
        stored = getattr(character, "professions", None)
        if isinstance(stored, dict) and not professions:
            professions = {
                str(k).lower(): int(v)
                for k, v in stored.items()
                if str(v).isdigit()
            }

    return TravelContext(
        faction=faction,
        character_class=character_class,
        level=level,
        professions=professions,
        hearthstone_map_id=payload.hearthstone_map_id,
        excluded_methods=frozenset(payload.exclude_methods),
    )


@router.get(
    "/zones",
    response_model=list[ZoneOut],
    summary="List known zones",
)
async def list_zones(
    expansion: str | None = None,
    continent: str | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> list[ZoneOut]:
    zones = list(ZONES.values())
    if expansion:
        zones = [z for z in zones if z.expansion.value == expansion]
    if continent:
        zones = [z for z in zones if z.continent.lower() == continent.lower()]
    if q:
        needle = q.lower()
        zones = [z for z in zones if needle in z.name.lower()]

    zones.sort(key=lambda z: (z.expansion.order, z.name))
    return [
        ZoneOut(
            map_id=z.map_id,
            name=z.name,
            expansion=z.expansion.value,
            continent=z.continent,
            level_range=z.level_range,
            faction=z.faction,
            is_city=z.is_city,
            flyable=z.flyable,
        )
        for z in zones
    ]


@router.post(
    "/route",
    summary="Plan travel between two zones",
    description=(
        "Finds the fastest route using every option the character can "
        "actually use — portals, mage teleports, Dreamwalk, engineering "
        "wormholes, flight paths, boats. Set `alternatives` above 1 for "
        "structurally different backup routes."
    ),
)
async def plan_route(
    payload: RouteRequest,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    for map_id in (payload.origin_map_id, payload.destination_map_id):
        if get_zone(map_id) is None:
            raise NotFoundError(f"Map id {map_id} is not a known zone.")

    context = await _context_from(payload, current_user.id, session)

    if payload.alternatives == 1:
        route = find_route(
            payload.origin_map_id, payload.destination_map_id, context
        )
        routes = [route] if route else []
    else:
        routes = find_routes(
            payload.origin_map_id,
            payload.destination_map_id,
            context,
            limit=payload.alternatives,
        )

    if not routes:
        origin = get_zone(payload.origin_map_id)
        destination = get_zone(payload.destination_map_id)
        return {
            "found": False,
            "reason": (
                f"No modelled route from {origin.name if origin else '?'} to "
                f"{destination.name if destination else '?'} with this "
                "character's capabilities."
            ),
            "routes": [],
        }

    return {
        "found": True,
        "routes": [route.as_dict() for route in routes],
        "best_seconds": round(routes[0].total_seconds, 1),
    }


@router.get(
    "/options/{map_id}",
    summary="Travel options leaving a zone",
)
async def travel_options(map_id: int) -> dict:
    zone = get_zone(map_id)
    if zone is None:
        raise NotFoundError(f"Map id {map_id} is not a known zone.")

    edges = edges_from(map_id)
    return {
        "map_id": map_id,
        "zone": zone.name,
        "option_count": len(edges),
        "options": [
            {
                "name": edge.name,
                "method": edge.method.value,
                "to_map_id": edge.target_map_id,
                "to_zone": (
                    get_zone(edge.target_map_id).name
                    if get_zone(edge.target_map_id)
                    else None
                ),
                "cost_seconds": edge.cost_seconds,
                "requirement": edge.requirement.describe(),
                "instant": edge.method.is_instant,
            }
            for edge in sorted(edges, key=lambda e: e.cost_seconds)
        ],
    }


@router.post(
    "/waypoints",
    summary="Generate a TomTom macro",
    description=(
        "Turns a list of coordinates into `/way` commands ready to paste "
        "into TomTom's `/ttpaste` window. Coordinates are percentages, as "
        "shown in game."
    ),
)
async def generate_waypoints(payload: WaypointRequest) -> dict:
    waypoints: list[Waypoint] = []
    for entry in payload.points:
        if get_zone(entry.map_id) is None:
            raise ValidationError(
                f"Map id {entry.map_id} is not a known zone.",
                details={"map_id": entry.map_id},
            )
        waypoints.append(
            Waypoint(
                point=MapPoint.from_percent(entry.map_id, entry.x, entry.y),
                description=entry.description,
            )
        )

    waypoint_set = WaypointSet(waypoints=tuple(waypoints), title=payload.title)
    result = waypoint_set.as_dict()
    result["macro"] = waypoint_set.to_macro(include_reset=payload.include_reset)
    return result


@router.get(
    "/methods",
    summary="Supported travel methods",
)
async def methods() -> list[dict]:
    return [
        {
            "method": method.value,
            "instant": method.is_instant,
            "convenience": method.convenience,
        }
        for method in sorted(TravelMethod, key=lambda m: m.convenience)
    ]


class NavigationModule(FeatureModule):
    """Registers navigation endpoints."""

    name = "navigation"
    description = (
        "Travel planning: portals, teleports, flight paths, and TomTom "
        "waypoint generation."
    )

    @property
    def router(self) -> APIRouter:
        return router

    async def startup(self) -> None:
        problems = validate_network()
        if problems:
            logger.error(
                "Travel network has %d integrity problem(s)", len(problems),
                extra={"problems": problems[:10]},
            )

    async def health(self) -> dict[str, str]:
        from app.navigation.travel import TRAVEL_EDGES

        return {
            "status": "ok",
            "zones": str(len(ZONES)),
            "travel_edges": str(len(TRAVEL_EDGES)),
        }


navigation_module = registry.register(NavigationModule())

__all__ = ["NavigationModule", "format_way_command", "navigation_module", "router"]
