"""Shared world geography: expansions, zones, and map coordinates.

Both the quest and navigation systems reason about *where* things are, so
the coordinate model lives here rather than in either one.

Coordinates follow the in-game convention: normalised 0..1 pairs within a
zone identified by its ``uiMapID``. That is what the client works in, what
TomTom consumes, and what Blizzard's own POI data returns, so no conversion
layer is needed at the edges.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "Expansion",
    "MapPoint",
    "Zone",
    "ZoneConnectionKind",
]


class Expansion(StrEnum):
    """Every WoW expansion, ordered by release.

    Ordering matters: campaign progression, level-range defaults, and
    "current content" checks all derive from :attr:`order` rather than
    hard-coded patch numbers, so shipping a new expansion is a one-line
    addition here plus its data pack.
    """

    CLASSIC = "classic"
    BURNING_CRUSADE = "burning_crusade"
    WRATH = "wrath"
    CATACLYSM = "cataclysm"
    PANDARIA = "pandaria"
    DRAENOR = "draenor"
    LEGION = "legion"
    BATTLE_FOR_AZEROTH = "battle_for_azeroth"
    SHADOWLANDS = "shadowlands"
    DRAGONFLIGHT = "dragonflight"
    WAR_WITHIN = "war_within"
    MIDNIGHT = "midnight"

    @property
    def order(self) -> int:
        return _EXPANSION_ORDER[self]

    @property
    def display_name(self) -> str:
        return _EXPANSION_NAMES[self]

    @property
    def level_range(self) -> tuple[int, int]:
        """Intended levelling band under the current level-squish."""
        return _EXPANSION_LEVELS[self]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Expansion):
            return NotImplemented
        return self.order < other.order


_EXPANSION_ORDER: dict[Expansion, int] = {
    expansion: index for index, expansion in enumerate(Expansion)
}

_EXPANSION_NAMES: dict[Expansion, str] = {
    Expansion.CLASSIC: "Classic",
    Expansion.BURNING_CRUSADE: "The Burning Crusade",
    Expansion.WRATH: "Wrath of the Lich King",
    Expansion.CATACLYSM: "Cataclysm",
    Expansion.PANDARIA: "Mists of Pandaria",
    Expansion.DRAENOR: "Warlords of Draenor",
    Expansion.LEGION: "Legion",
    Expansion.BATTLE_FOR_AZEROTH: "Battle for Azeroth",
    Expansion.SHADOWLANDS: "Shadowlands",
    Expansion.DRAGONFLIGHT: "Dragonflight",
    Expansion.WAR_WITHIN: "The War Within",
    Expansion.MIDNIGHT: "Midnight",
}

# Post-squish levelling bands. Chromie Time makes most of these overlap in
# practice, which is exactly why the recommender treats them as hints rather
# than hard gates.
_EXPANSION_LEVELS: dict[Expansion, tuple[int, int]] = {
    Expansion.CLASSIC: (1, 60),
    Expansion.BURNING_CRUSADE: (10, 30),
    Expansion.WRATH: (10, 30),
    Expansion.CATACLYSM: (10, 30),
    Expansion.PANDARIA: (10, 35),
    Expansion.DRAENOR: (10, 40),
    Expansion.LEGION: (10, 45),
    Expansion.BATTLE_FOR_AZEROTH: (10, 50),
    Expansion.SHADOWLANDS: (48, 60),
    Expansion.DRAGONFLIGHT: (58, 70),
    Expansion.WAR_WITHIN: (68, 80),
    Expansion.MIDNIGHT: (78, 90),
}


@dataclass(frozen=True, slots=True)
class MapPoint:
    """A point inside a zone, in normalised in-game map coordinates.

    ``x`` and ``y`` are 0..1 as the client reports them. TomTom displays
    them multiplied by 100, which :mod:`app.navigation.tomtom` handles at
    the formatting boundary — internally everything stays normalised so
    distance maths never mixes units.
    """

    map_id: int
    x: float
    y: float

    def __post_init__(self) -> None:
        if self.map_id <= 0:
            raise ValueError(f"map_id must be positive, got {self.map_id}")
        for axis, value in (("x", self.x), ("y", self.y)):
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"{axis} must be normalised to 0..1, got {value}. "
                    "Divide TomTom-style percentages by 100."
                )

    @classmethod
    def from_percent(cls, map_id: int, x: float, y: float) -> MapPoint:
        """Build from TomTom-style percentages (``45.2 67.8``)."""
        return cls(map_id=map_id, x=x / 100.0, y=y / 100.0)

    @property
    def as_percent(self) -> tuple[float, float]:
        return (round(self.x * 100, 2), round(self.y * 100, 2))

    def planar_distance(self, other: MapPoint) -> float:
        """Normalised distance to another point in the *same* zone.

        Raises when the zones differ: silently returning a number for
        points in different zones would produce confident nonsense in the
        route optimiser. Cross-zone cost belongs to the travel graph.
        """
        if self.map_id != other.map_id:
            raise ValueError(
                f"Cannot measure planar distance across zones "
                f"({self.map_id} vs {other.map_id}); use the travel graph."
            )
        return math.hypot(self.x - other.x, self.y - other.y)

    def __str__(self) -> str:
        x, y = self.as_percent
        return f"{x:.1f}, {y:.1f} (map {self.map_id})"


class ZoneConnectionKind(StrEnum):
    """How two zones connect, which determines the travel cost model."""

    #: Walk or fly directly across a shared border.
    ADJACENT = "adjacent"
    #: A zeppelin, boat, or tram on a fixed schedule.
    TRANSPORT = "transport"
    #: A static portal both factions or one faction can use.
    PORTAL = "portal"
    #: Continents that require a taxi, portal, or transport hop.
    SEPARATE_CONTINENT = "separate_continent"


@dataclass(frozen=True, slots=True)
class Zone:
    """A playable zone keyed by its in-game ``uiMapID``."""

    map_id: int
    name: str
    expansion: Expansion
    continent: str
    #: Suggested level band; ``None`` for level-agnostic zones such as cities.
    level_range: tuple[int, int] | None = None
    #: Faction-restricted hubs ("alliance"/"horde"); ``None`` when neutral.
    faction: str | None = None
    is_city: bool = False
    #: Whether flying is available (affects intra-zone traversal speed).
    flyable: bool = True

    def __post_init__(self) -> None:
        if self.map_id <= 0:
            raise ValueError(f"{self.name}: map_id must be positive")
        if not self.name.strip():
            raise ValueError(f"Zone {self.map_id} has no name")

    def point(self, x: float, y: float) -> MapPoint:
        """Build a percentage-based point inside this zone."""
        return MapPoint.from_percent(self.map_id, x, y)
