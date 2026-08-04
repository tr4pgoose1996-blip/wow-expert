"""Turn quest routes into TomTom waypoint sets.

The bridge between :mod:`app.quests.optimizer` and
:mod:`app.navigation.tomtom`: it takes a planned route and emits the macro a
player pastes into the game.

Kept separate from both so neither depends on the other — the optimiser knows
nothing about addons, and TomTom formatting knows nothing about quests.
"""

from __future__ import annotations

from app.navigation.tomtom import Waypoint, WaypointSet
from app.quests.models import Quest
from app.quests.optimizer import OptimizedRoute
from app.world.zones import get_zone

__all__ = [
    "quest_waypoints",
    "route_waypoints",
]


def route_waypoints(route: OptimizedRoute, *, title: str = "") -> WaypointSet:
    """Every stop on an optimised route, in visit order.

    Waypoints are numbered so the ordering survives inside the game: TomTom
    lists them without any notion of a route, and an unnumbered list of
    twenty points gives the player no idea which to do first.
    """
    waypoints: list[Waypoint] = []
    index = 1

    for leg in route.legs:
        zone = get_zone(leg.map_id)
        group = zone.name if zone else f"Map {leg.map_id}"
        for point, label in leg.waypoints:
            waypoints.append(
                Waypoint(
                    point=point,
                    description=f"{index}. {label}",
                    group=group,
                )
            )
            index += 1

    if not title:
        zones = route.as_dict()["zone_count"]
        minutes = int(route.total_seconds // 60)
        title = (
            f"wow! expert route: {len(route.ordered_quests)} quests, "
            f"{zones} zone(s), ~{minutes} min"
        )

    return WaypointSet(waypoints=tuple(waypoints), title=title)


def quest_waypoints(quest: Quest) -> WaypointSet:
    """Waypoints for a single quest: pick-up, objectives, turn-in."""
    waypoints: list[Waypoint] = []

    if quest.start_point is not None:
        label = "Pick up"
        if quest.start_npc:
            label += f" from {quest.start_npc}"
        waypoints.append(
            Waypoint(point=quest.start_point, description=label, group=quest.title)
        )

    for objective in quest.objectives:
        if objective.location is not None:
            waypoints.append(
                Waypoint(
                    point=objective.location,
                    description=objective.description,
                    group=quest.title,
                )
            )

    end = quest.end_point
    if end is not None and end != quest.start_point:
        label = "Turn in"
        if quest.end_npc:
            label += f" to {quest.end_npc}"
        waypoints.append(Waypoint(point=end, description=label, group=quest.title))

    return WaypointSet(waypoints=tuple(waypoints), title=quest.title)
