"""TomTom waypoint generation.

Emits ``/way`` commands in the syntax TomTom actually parses, verified
against the addon's own documentation (v4.3.8):

* ``/way #<uiMapID> <x> <y> <description>`` — explicit map id
* ``/way <Zone Name> <x> <y> <description>`` — zone by name
* ``/way <x> <y> <description>`` — current zone
* ``/way reset all``

The ``#<uiMapID>`` form is used for generated output because zone names are
localised and ambiguous ("Nagrand" and "Shadowmoon Valley" each exist twice),
while map ids are stable and unambiguous.

TomTom shows coordinates as percentages, so the 0..1 internal representation
is converted here — the single place that conversion happens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.world.geography import MapPoint
from app.world.zones import get_zone

__all__ = [
    "Waypoint",
    "WaypointSet",
    "format_way_command",
    "parse_way_command",
]

#: TomTom reads the rest of the line as the description, so newlines would
#: split one waypoint into two commands and a chat message. Pipe characters
#: begin UI escape sequences in WoW's chat and must not survive either.
_UNSAFE_IN_DESCRIPTION = re.compile(r"[\r\n|]+")

#: Matches the generated form on the way back in.
_WAY_PATTERN = re.compile(
    r"^/way\s+#(?P<map_id>\d+)\s+"
    r"(?P<x>\d+(?:\.\d+)?)\s+"
    r"(?P<y>\d+(?:\.\d+)?)"
    r"(?:\s+(?P<description>.*))?$"
)


def _sanitise(description: str) -> str:
    """Make a description safe to append to a slash command."""
    cleaned = _UNSAFE_IN_DESCRIPTION.sub(" ", description).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    # WoW's chat input caps at 255 characters; leave room for the command.
    return cleaned[:180]


@dataclass(frozen=True, slots=True)
class Waypoint:
    """A single navigable point with a label."""

    point: MapPoint
    description: str = ""
    #: Optional grouping tag (quest name, route leg) for the header comment.
    group: str = ""

    @property
    def zone_name(self) -> str:
        zone = get_zone(self.point.map_id)
        return zone.name if zone else f"Map {self.point.map_id}"

    def to_command(self) -> str:
        return format_way_command(self.point, self.description)


def format_way_command(point: MapPoint, description: str = "") -> str:
    """Render one ``/way`` command.

    Coordinates are emitted to two decimals — TomTom's own display precision,
    and about 5 yards in a large zone, which is well inside the addon's
    arrival radius. More precision would be false confidence.
    """
    x, y = point.as_percent
    command = f"/way #{point.map_id} {x:.2f} {y:.2f}"
    cleaned = _sanitise(description)
    return f"{command} {cleaned}" if cleaned else command


def parse_way_command(line: str) -> Waypoint | None:
    """Parse a generated ``/way`` command back into a waypoint.

    Round-tripping is what makes the output testable, and lets users paste
    their own waypoint lists in for optimisation.
    """
    match = _WAY_PATTERN.match(line.strip())
    if match is None:
        return None
    try:
        point = MapPoint.from_percent(
            map_id=int(match["map_id"]),
            x=float(match["x"]),
            y=float(match["y"]),
        )
    except ValueError:
        # Out-of-range coordinates: a malformed line, not a waypoint.
        return None
    return Waypoint(point=point, description=(match["description"] or "").strip())


@dataclass(frozen=True, slots=True)
class WaypointSet:
    """An ordered list of waypoints, renderable as a pasteable macro."""

    waypoints: tuple[Waypoint, ...]
    title: str = ""

    def __len__(self) -> int:
        return len(self.waypoints)

    def __bool__(self) -> bool:
        return bool(self.waypoints)

    @property
    def zones(self) -> tuple[str, ...]:
        """Distinct zone names in visit order."""
        ordered: list[str] = []
        for waypoint in self.waypoints:
            if not ordered or ordered[-1] != waypoint.zone_name:
                ordered.append(waypoint.zone_name)
        return tuple(ordered)

    def to_commands(self) -> list[str]:
        return [w.to_command() for w in self.waypoints]

    def to_macro(self, *, include_reset: bool = True, comment: bool = True) -> str:
        """Render the full block for pasting into TomTom's ``/ttpaste``.

        ``include_reset`` clears existing waypoints first, which is almost
        always wanted: leftover waypoints from a previous route make the
        crazy arrow point at the wrong objective.
        """
        lines: list[str] = []
        if comment and self.title:
            # TomTom ignores lines it cannot parse, so a bare header line is
            # safe and helps the player see what they pasted.
            lines.append(f"# {_sanitise(self.title)}")
        if include_reset:
            lines.append("/way reset all")
        lines.extend(self.to_commands())
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "count": len(self.waypoints),
            "zones": list(self.zones),
            "macro": self.to_macro(),
            "waypoints": [
                {
                    "map_id": w.point.map_id,
                    "zone": w.zone_name,
                    "x": w.point.as_percent[0],
                    "y": w.point.as_percent[1],
                    "description": w.description,
                    "group": w.group,
                    "command": w.to_command(),
                }
                for w in self.waypoints
            ],
        }
