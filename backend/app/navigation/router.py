"""Route planning across the travel network.

Dijkstra's algorithm over :mod:`app.navigation.travel`, filtered by what the
character can actually use, with support for returning several genuinely
different routes rather than minor variations of one.

Why Dijkstra and not A*: a useful A* heuristic needs an admissible lower
bound on remaining travel time, but portals make real distance wildly
non-Euclidean — two zones on opposite continents can be one 20-second hop
apart. Any geographic heuristic would be inadmissible and could return a
non-optimal route. The graph is small (hundreds of edges), so exact search
is both fast and correct.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.navigation.travel import (
    _MAGE_DESTINATIONS,
    MAGE_PORTAL_COST,
    TRAVEL_EDGES,
    TravelEdge,
    TravelMethod,
    TravelRequirement,
)
from app.world.geography import MapPoint
from app.world.zones import get_zone

logger = get_logger(__name__)

__all__ = [
    "Route",
    "RouteStep",
    "TravelContext",
    "estimate_intra_zone_seconds",
    "find_route",
    "find_routes",
]

#: Seconds to cross an entire zone at current-expansion flight speed. Used to
#: price movement within a zone, which pure graph distance ignores.
ZONE_CROSSING_SECONDS = 70.0
#: Ground movement is roughly this much slower than flying.
GROUND_PENALTY = 2.2


def estimate_intra_zone_seconds(
    start: MapPoint, end: MapPoint, *, flyable: bool = True
) -> float:
    """Estimate travel time between two points in the same zone."""
    distance = start.planar_distance(end)
    seconds = distance * ZONE_CROSSING_SECONDS
    return seconds if flyable else seconds * GROUND_PENALTY


@dataclass(frozen=True, slots=True)
class TravelContext:
    """The character's capabilities, which determine usable routes."""

    faction: str | None = None
    character_class: str | None = None
    level: int | None = None
    professions: dict[str, int] = field(default_factory=dict)
    #: Where the hearthstone is bound, enabling it as a routing option.
    hearthstone_map_id: int | None = None
    #: Exclude options on cooldown, or those the player prefers to save.
    excluded_methods: frozenset[TravelMethod] = frozenset()

    def can_use(self, edge: TravelEdge) -> bool:
        if edge.method in self.excluded_methods:
            return False
        return edge.is_available_to(
            faction=self.faction,
            character_class=self.character_class,
            professions=self.professions,
            level=self.level,
        )


@dataclass(frozen=True, slots=True)
class RouteStep:
    """One leg of a journey."""

    edge: TravelEdge
    cumulative_seconds: float

    @property
    def from_map_id(self) -> int:
        return self.edge.source_map_id

    @property
    def to_map_id(self) -> int:
        return self.edge.target_map_id

    def describe(self) -> str:
        source = get_zone(self.from_map_id)
        target = get_zone(self.to_map_id)
        source_name = source.name if source else f"map {self.from_map_id}"
        target_name = target.name if target else f"map {self.to_map_id}"
        return f"{self.edge.name}: {source_name} \u2192 {target_name}"


@dataclass(frozen=True, slots=True)
class Route:
    """A complete journey between two zones."""

    steps: tuple[RouteStep, ...]
    total_seconds: float
    origin_map_id: int
    destination_map_id: int

    @property
    def is_trivial(self) -> bool:
        """True when origin and destination are the same zone."""
        return not self.steps

    @property
    def methods(self) -> tuple[TravelMethod, ...]:
        return tuple(step.edge.method for step in self.steps)

    @property
    def requirements(self) -> tuple[TravelRequirement, ...]:
        """Non-trivial requirements along the route, for display."""
        return tuple(
            step.edge.requirement
            for step in self.steps
            if not step.edge.requirement.is_open
        )

    @property
    def summary(self) -> str:
        if self.is_trivial:
            return "Already in the destination zone."
        return " \u2192 ".join(step.edge.name for step in self.steps)

    def as_dict(self) -> dict:
        return {
            "origin_map_id": self.origin_map_id,
            "destination_map_id": self.destination_map_id,
            "total_seconds": round(self.total_seconds, 1),
            "step_count": len(self.steps),
            "summary": self.summary,
            "steps": [
                {
                    "name": step.edge.name,
                    "method": step.edge.method.value,
                    "from_map_id": step.from_map_id,
                    "to_map_id": step.to_map_id,
                    "cost_seconds": round(step.edge.cost_seconds, 1),
                    "cumulative_seconds": round(step.cumulative_seconds, 1),
                    "requirement": step.edge.requirement.describe(),
                }
                for step in self.steps
            ],
        }


def _origin_specific_edges(
    origin_map_id: int, context: TravelContext
) -> list[TravelEdge]:
    """Edges that exist only because of where the character is standing.

    Mage teleports and hearthstones are castable from anywhere, so they are
    not in the static network (which would need an edge from every zone to
    every hub). They are synthesised here for the actual origin.
    """
    extra: list[TravelEdge] = []

    if context.character_class == "mage":
        for target, spell, faction in _MAGE_DESTINATIONS:
            if target == origin_map_id:
                continue
            if faction and context.faction and faction != context.faction:
                continue
            extra.append(
                TravelEdge(
                    source_map_id=origin_map_id,
                    target_map_id=target,
                    method=TravelMethod.MAGE_PORTAL,
                    cost_seconds=MAGE_PORTAL_COST,
                    name=spell,
                    requirement=TravelRequirement(
                        character_class="mage", faction=faction, min_level=10
                    ),
                )
            )

    hearth = context.hearthstone_map_id
    if hearth is not None and hearth != origin_map_id:
        zone = get_zone(hearth)
        extra.append(
            TravelEdge(
                source_map_id=origin_map_id,
                target_map_id=hearth,
                method=TravelMethod.HEARTHSTONE,
                cost_seconds=30.0,
                name=f"Hearthstone to {zone.name if zone else hearth}",
                requirement=TravelRequirement(
                    cooldown_seconds=900, notes=("Hearthstone",)
                ),
            )
        )

    return extra


def _build_adjacency(
    context: TravelContext, origin_map_id: int
) -> dict[int, list[TravelEdge]]:
    """Usable outbound edges keyed by source zone."""
    adjacency: dict[int, list[TravelEdge]] = {}
    for edge in TRAVEL_EDGES:
        if context.can_use(edge):
            adjacency.setdefault(edge.source_map_id, []).append(edge)
    for edge in _origin_specific_edges(origin_map_id, context):
        if context.can_use(edge):
            adjacency.setdefault(edge.source_map_id, []).append(edge)
    return adjacency


def find_route(
    origin_map_id: int,
    destination_map_id: int,
    context: TravelContext | None = None,
    *,
    banned_edges: frozenset[tuple[int, int, str]] = frozenset(),
) -> Route | None:
    """Cheapest route between two zones, or ``None`` if unreachable.

    ``banned_edges`` excludes specific edges, which :func:`find_routes` uses
    to force genuinely distinct alternatives.
    """
    context = context or TravelContext()

    if origin_map_id == destination_map_id:
        return Route(
            steps=(),
            total_seconds=0.0,
            origin_map_id=origin_map_id,
            destination_map_id=destination_map_id,
        )

    adjacency = _build_adjacency(context, origin_map_id)

    # (cost, convenience, counter, zone, path). The counter keeps heap
    # comparisons total without ever comparing TravelEdge objects.
    counter = 0
    frontier: list[tuple[float, int, int, int, tuple[TravelEdge, ...]]] = [
        (0.0, 0, counter, origin_map_id, ())
    ]
    best_cost: dict[int, float] = {origin_map_id: 0.0}

    while frontier:
        cost, convenience, _, zone_id, path = heapq.heappop(frontier)

        if zone_id == destination_map_id:
            steps: list[RouteStep] = []
            running = 0.0
            for edge in path:
                running += edge.cost_seconds
                steps.append(RouteStep(edge=edge, cumulative_seconds=running))
            return Route(
                steps=tuple(steps),
                total_seconds=cost,
                origin_map_id=origin_map_id,
                destination_map_id=destination_map_id,
            )

        if cost > best_cost.get(zone_id, float("inf")):
            continue

        for edge in adjacency.get(zone_id, ()):
            key = (edge.source_map_id, edge.target_map_id, edge.name)
            if key in banned_edges:
                continue
            new_cost = cost + edge.cost_seconds
            if new_cost >= best_cost.get(edge.target_map_id, float("inf")):
                continue
            best_cost[edge.target_map_id] = new_cost
            counter += 1
            heapq.heappush(
                frontier,
                (
                    new_cost,
                    convenience + edge.method.convenience,
                    counter,
                    edge.target_map_id,
                    (*path, edge),
                ),
            )

    return None


def find_routes(
    origin_map_id: int,
    destination_map_id: int,
    context: TravelContext | None = None,
    *,
    limit: int = 3,
) -> list[Route]:
    """Several distinct routes, cheapest first.

    Uses an edge-banning variant of Yen's algorithm: after finding the best
    route, ban one of its edges and search again. This yields alternatives
    that differ structurally — a different portal, a different hub — rather
    than the same journey with one leg swapped, which is what a player
    actually wants when the first option is on cooldown.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")

    context = context or TravelContext()
    best = find_route(origin_map_id, destination_map_id, context)
    if best is None:
        return []
    if best.is_trivial:
        return [best]

    routes = [best]
    seen_signatures = {tuple(s.edge.name for s in best.steps)}

    # Ban each edge of the best route in turn. Banning the cheapest-first
    # edges tends to produce the most distinct alternatives.
    for step in sorted(best.steps, key=lambda s: -s.edge.cost_seconds):
        if len(routes) >= limit:
            break
        banned = frozenset(
            {(step.edge.source_map_id, step.edge.target_map_id, step.edge.name)}
        )
        alternative = find_route(
            origin_map_id, destination_map_id, context, banned_edges=banned
        )
        if alternative is None or alternative.is_trivial:
            continue
        signature = tuple(s.edge.name for s in alternative.steps)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        routes.append(alternative)

    routes.sort(key=lambda r: r.total_seconds)
    return routes[:limit]
