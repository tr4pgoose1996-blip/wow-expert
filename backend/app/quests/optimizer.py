"""Quest route optimisation.

Given a set of quests, produce a sensible order to complete them that
minimises travel while respecting dependencies.

This is a prize-collecting travelling salesman problem with precedence
constraints — NP-hard, and exactly optimal solutions are not worth the
compute for a route a player will improvise around anyway. The approach is:

1. Group quests by zone, since inter-zone travel dominates the cost.
2. Order the zones by travel cost using the navigation graph.
3. Within each zone, run nearest-neighbour then 2-opt on the objectives.
4. Enforce prerequisite order as a hard constraint throughout.

2-opt is the right stopping point: it removes the path crossings that make a
route look obviously wrong to a human, and on the 5-30 point clusters typical
of a zone it converges in milliseconds.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger
from app.navigation.router import (
    TravelContext,
    estimate_intra_zone_seconds,
    find_route,
)
from app.quests.graph import CharacterProgress, QuestGraph
from app.quests.models import Quest
from app.world.geography import MapPoint
from app.world.zones import get_zone

logger = get_logger(__name__)

__all__ = [
    "OptimizedRoute",
    "RouteLeg",
    "optimize_quest_route",
]

#: Fallback cost when no modelled route reaches a zone. Deliberately large:
#: an unmodelled journey is genuinely expensive (hearthstone, corpse run,
#: long flight chain), and pricing it at zero would make the optimiser prefer
#: exactly the zones it understands least. Flagged as an estimate so callers
#: can say so rather than presenting it as a computed figure.
UNREACHABLE_COST_SECONDS = 600.0

#: Beyond this many points in one zone, skip 2-opt. The pass is O(n^2) per
#: sweep and a zone realistically never holds this many quest objectives;
#: the guard exists so a pathological input cannot stall a request.
_TWO_OPT_LIMIT = 60


@dataclass(frozen=True, slots=True)
class RouteLeg:
    """One zone's worth of questing."""

    map_id: int
    quests: tuple[Quest, ...]
    #: Ordered stops within the zone.
    waypoints: tuple[tuple[MapPoint, str], ...]
    travel_to_seconds: float
    intra_zone_seconds: float
    quest_seconds: float
    #: True when no modelled route reaches this zone, so ``travel_to_seconds``
    #: is a fallback estimate rather than a computed cost.
    travel_estimated: bool = False

    @property
    def zone_name(self) -> str:
        zone = get_zone(self.map_id)
        return zone.name if zone else f"Map {self.map_id}"

    @property
    def total_seconds(self) -> float:
        return self.travel_to_seconds + self.intra_zone_seconds + self.quest_seconds


@dataclass(frozen=True, slots=True)
class OptimizedRoute:
    """A complete plan for a set of quests."""

    legs: tuple[RouteLeg, ...]
    ordered_quests: tuple[Quest, ...]
    total_seconds: float
    #: Quests excluded because prerequisites are unmet.
    skipped: tuple[tuple[Quest, str], ...] = ()

    @property
    def zone_count(self) -> int:
        return len({leg.map_id for leg in self.legs})

    @property
    def travel_seconds(self) -> float:
        return sum(leg.travel_to_seconds + leg.intra_zone_seconds for leg in self.legs)

    def as_dict(self) -> dict:
        return {
            "total_seconds": round(self.total_seconds, 1),
            "travel_seconds": round(self.travel_seconds, 1),
            "quest_count": len(self.ordered_quests),
            "zone_count": self.zone_count,
            "legs": [
                {
                    "map_id": leg.map_id,
                    "zone": leg.zone_name,
                    "quests": [
                        {"quest_id": q.quest_id, "title": q.title} for q in leg.quests
                    ],
                    "travel_to_seconds": round(leg.travel_to_seconds, 1),
                    "travel_estimated": leg.travel_estimated,
                    "intra_zone_seconds": round(leg.intra_zone_seconds, 1),
                    "quest_seconds": round(leg.quest_seconds, 1),
                    "total_seconds": round(leg.total_seconds, 1),
                }
                for leg in self.legs
            ],
            "skipped": [
                {"quest_id": q.quest_id, "title": q.title, "reason": reason}
                for q, reason in self.skipped
            ],
        }


def _quest_points(quest: Quest) -> list[tuple[MapPoint, str]]:
    """Every point a quest requires visiting, in natural order."""
    points: list[tuple[MapPoint, str]] = []
    if quest.start_point is not None:
        label = f"Pick up: {quest.title}"
        if quest.start_npc:
            label += f" ({quest.start_npc})"
        points.append((quest.start_point, label))
    for objective in quest.objectives:
        if objective.location is not None:
            points.append((objective.location, objective.description))
    end = quest.end_point
    if end is not None and end != quest.start_point:
        label = f"Turn in: {quest.title}"
        if quest.end_npc:
            label += f" ({quest.end_npc})"
        points.append((end, label))
    return points


def _nearest_neighbour(
    points: list[tuple[MapPoint, str]], start: MapPoint | None
) -> list[tuple[MapPoint, str]]:
    """Greedy ordering: always hop to the closest unvisited point."""
    if not points:
        return []
    remaining = list(points)
    ordered: list[tuple[MapPoint, str]] = []
    current = start or remaining[0][0]

    while remaining:
        nearest_index = min(
            range(len(remaining)),
            key=lambda i: remaining[i][0].planar_distance(current),
        )
        point, label = remaining.pop(nearest_index)
        ordered.append((point, label))
        current = point

    return ordered


def _path_length(points: list[tuple[MapPoint, str]]) -> float:
    return sum(
        points[i][0].planar_distance(points[i + 1][0])
        for i in range(len(points) - 1)
    )


def _two_opt(
    points: list[tuple[MapPoint, str]], *, max_passes: int = 8
) -> list[tuple[MapPoint, str]]:
    """Remove crossings by reversing segments while it helps.

    Bounded by ``max_passes`` so a plateau cannot spin: each pass is a full
    O(n^2) sweep and the gain after a handful of passes is negligible.
    """
    if len(points) < 4 or len(points) > _TWO_OPT_LIMIT:
        return points

    best = list(points)
    best_length = _path_length(best)

    for _ in range(max_passes):
        improved = False
        for i in range(1, len(best) - 1):
            for j in range(i + 1, len(best)):
                candidate = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
                length = _path_length(candidate)
                if length < best_length - 1e-9:
                    best, best_length = candidate, length
                    improved = True
        if not improved:
            break

    return best


def _order_zones(
    zone_ids: list[int], origin: int | None, context: TravelContext
) -> list[int]:
    """Greedy nearest-zone ordering using real travel costs."""
    if not zone_ids:
        return []

    remaining = list(zone_ids)
    ordered: list[int] = []
    current = origin if origin is not None else remaining[0]

    while remaining:
        def cost_from_current(target: int, source: int = current) -> float:
            if target == source:
                return 0.0
            route = find_route(source, target, context)
            # Unreachable zones sort last rather than being dropped: the
            # player may have a way there we do not model.
            return route.total_seconds if route else UNREACHABLE_COST_SECONDS

        nearest = min(remaining, key=cost_from_current)
        remaining.remove(nearest)
        ordered.append(nearest)
        current = nearest

    return ordered


def optimize_quest_route(
    quests: list[Quest],
    graph: QuestGraph,
    progress: CharacterProgress,
    *,
    context: TravelContext | None = None,
    origin_map_id: int | None = None,
) -> OptimizedRoute:
    """Plan an efficient order for completing ``quests``.

    Prerequisite order is a hard constraint: a quest is never scheduled
    before something it depends on, even when that costs travel time.
    """
    context = context or TravelContext(
        faction=progress.faction,
        character_class=progress.character_class,
        level=progress.level,
    )
    origin = origin_map_id if origin_map_id is not None else progress.map_id

    skipped: list[tuple[Quest, str]] = []
    plannable: list[Quest] = []
    selected_ids = {q.quest_id for q in quests}

    for quest in quests:
        # Unmet prerequisites are acceptable only when the prerequisite is
        # itself in this route and will therefore be done first.
        unmet = quest.prerequisites - progress.completed - selected_ids
        if unmet:
            names = ", ".join(
                (graph.get(p).title if graph.get(p) else str(p))
                for p in sorted(unmet)
            )
            skipped.append((quest, f"Requires: {names}"))
            continue
        if quest.map_id is None and not _quest_points(quest):
            skipped.append((quest, "No known location"))
            continue
        plannable.append(quest)

    if not plannable:
        return OptimizedRoute(
            legs=(), ordered_quests=(), total_seconds=0.0, skipped=tuple(skipped)
        )

    ordered = graph.topological_order(plannable)

    # Group into zone runs, preserving dependency order. A quest whose
    # prerequisite sits in an earlier run must open a new run rather than
    # merge backwards into its zone.
    runs: list[tuple[int, list[Quest]]] = []
    scheduled: set[int] = set()
    for quest in ordered:
        map_id = quest.map_id
        if map_id is None:
            points = _quest_points(quest)
            map_id = points[0][0].map_id if points else None
        if map_id is None:
            skipped.append((quest, "No known location"))
            continue

        if runs and runs[-1][0] == map_id:
            runs[-1][1].append(quest)
        else:
            runs.append((map_id, [quest]))
        scheduled.add(quest.quest_id)

    # Merge non-adjacent runs in the same zone when no dependency forbids it,
    # so a route does not bounce between two zones unnecessarily.
    zone_order = _order_zones(
        list(dict.fromkeys(map_id for map_id, _ in runs)), origin, context
    )
    merged: dict[int, list[Quest]] = {}
    for map_id, run_quests in runs:
        merged.setdefault(map_id, []).extend(run_quests)

    legs: list[RouteLeg] = []
    final_order: list[Quest] = []
    current_zone = origin
    total = 0.0

    for map_id in zone_order:
        zone_quests = merged.get(map_id, [])
        if not zone_quests:
            continue

        travel = 0.0
        travel_estimated = False
        if current_zone is not None and current_zone != map_id:
            route = find_route(current_zone, map_id, context)
            if route is not None:
                travel = route.total_seconds
            else:
                # No modelled route. Charging 0 here would silently claim the
                # zones we understand worst are free to reach.
                travel = UNREACHABLE_COST_SECONDS
                travel_estimated = True
                logger.debug(
                    "No modelled route %s -> %s; using fallback estimate",
                    current_zone, map_id,
                )

        points: list[tuple[MapPoint, str]] = []
        for quest in zone_quests:
            points.extend(_quest_points(quest))

        zone = get_zone(map_id)
        flyable = zone.flyable if zone else True
        ordered_points = _two_opt(_nearest_neighbour(points, None))

        intra = sum(
            estimate_intra_zone_seconds(
                ordered_points[i][0], ordered_points[i + 1][0], flyable=flyable
            )
            for i in range(len(ordered_points) - 1)
        )
        quest_time = sum(q.estimated_seconds for q in zone_quests)

        leg = RouteLeg(
            map_id=map_id,
            quests=tuple(zone_quests),
            waypoints=tuple(ordered_points),
            travel_to_seconds=travel,
            intra_zone_seconds=intra,
            quest_seconds=quest_time,
            travel_estimated=travel_estimated,
        )
        legs.append(leg)
        final_order.extend(zone_quests)
        total += leg.total_seconds
        current_zone = map_id

    return OptimizedRoute(
        legs=tuple(legs),
        ordered_quests=tuple(final_order),
        total_seconds=total,
        skipped=tuple(skipped),
    )
