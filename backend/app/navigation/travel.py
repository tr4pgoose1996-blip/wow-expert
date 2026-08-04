"""Travel network: portals, flight paths, teleports, and their costs.

The model is a weighted directed graph. Nodes are zones; edges are ways to
move between them, each with a time cost in seconds and a set of
requirements the character must satisfy.

Two design points worth stating up front:

* **Edges are directed.** Many real WoW travel options are one-way — the
  Caverns of Time portal drops you in Tanaris but there is no return portal,
  and most class teleports go to a hub without a matching way back. Modelling
  everything as bidirectional would invent routes that do not exist.

* **Costs are calibrated in seconds**, estimated from typical play, not
  invented units. They are deliberately conservative: a route that promises
  30 seconds and takes 90 is worse than one that promises 90 honestly.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from app.world.geography import MapPoint

__all__ = [
    "TRAVEL_EDGES",
    "TravelEdge",
    "TravelMethod",
    "TravelRequirement",
    "edges_from",
    "register_edges",
    "validate_network",
]


class TravelMethod(StrEnum):
    """How a character moves along an edge.

    Ordered loosely by convenience, which the route planner uses to break
    ties when two routes cost the same: an instant portal beats a flight
    path of equal duration because it needs no attention from the player.
    """

    PORTAL = "portal"
    MAGE_PORTAL = "mage_portal"
    HEARTHSTONE = "hearthstone"
    TELEPORT_ITEM = "teleport_item"
    ENGINEERING_TELEPORT = "engineering_teleport"
    DREAMWALK = "dreamwalk"
    CLASS_TELEPORT = "class_teleport"
    FLIGHT_PATH = "flight_path"
    BOAT = "boat"
    ZEPPELIN = "zeppelin"
    TRAM = "tram"
    WALK = "walk"
    FLY = "fly"

    @property
    def is_instant(self) -> bool:
        """Whether arrival is effectively immediate after the cast."""
        return self in _INSTANT_METHODS

    @property
    def convenience(self) -> int:
        """Lower is better. Tie-breaker for equal-cost routes."""
        return _CONVENIENCE[self]


_INSTANT_METHODS = frozenset(
    {
        TravelMethod.PORTAL,
        TravelMethod.MAGE_PORTAL,
        TravelMethod.HEARTHSTONE,
        TravelMethod.TELEPORT_ITEM,
        TravelMethod.ENGINEERING_TELEPORT,
        TravelMethod.DREAMWALK,
        TravelMethod.CLASS_TELEPORT,
    }
)

_CONVENIENCE: dict[TravelMethod, int] = {
    TravelMethod.PORTAL: 0,
    TravelMethod.MAGE_PORTAL: 1,
    TravelMethod.DREAMWALK: 1,
    TravelMethod.CLASS_TELEPORT: 2,
    TravelMethod.TELEPORT_ITEM: 2,
    TravelMethod.ENGINEERING_TELEPORT: 3,
    TravelMethod.HEARTHSTONE: 4,
    TravelMethod.TRAM: 5,
    TravelMethod.BOAT: 6,
    TravelMethod.ZEPPELIN: 6,
    TravelMethod.FLIGHT_PATH: 7,
    TravelMethod.FLY: 8,
    TravelMethod.WALK: 9,
}


@dataclass(frozen=True, slots=True)
class TravelRequirement:
    """What a character needs before an edge is usable.

    Every field is optional; an empty requirement is satisfied by everyone.
    Requirements are checked before an edge is considered, so a route is
    never returned that the character cannot actually take.
    """

    faction: str | None = None
    character_class: str | None = None
    profession: str | None = None
    profession_skill: int | None = None
    min_level: int | None = None
    #: Human-readable notes (attunements, quest chains, reputation).
    notes: tuple[str, ...] = ()
    #: Whether the option is limited by a cooldown rather than always ready.
    cooldown_seconds: int | None = None

    @property
    def is_open(self) -> bool:
        return (
            self.faction is None
            and self.character_class is None
            and self.profession is None
            and self.min_level is None
        )

    def describe(self) -> str:
        parts: list[str] = []
        if self.faction:
            parts.append(self.faction.title())
        if self.character_class:
            parts.append(self.character_class.replace("_", " ").title())
        if self.profession:
            skill = f" {self.profession_skill}" if self.profession_skill else ""
            parts.append(f"{self.profession.title()}{skill}")
        if self.min_level:
            parts.append(f"level {self.min_level}+")
        parts.extend(self.notes)
        return ", ".join(parts) if parts else "no requirements"


@dataclass(frozen=True, slots=True)
class TravelEdge:
    """A directed way to get from one zone to another."""

    source_map_id: int
    target_map_id: int
    method: TravelMethod
    #: Estimated travel time in seconds, including cast and loading.
    cost_seconds: float
    name: str
    requirement: TravelRequirement = field(default_factory=TravelRequirement)
    #: Where the character must stand to use it, when it is a fixed object.
    origin_point: MapPoint | None = None
    #: Where the character arrives.
    arrival_point: MapPoint | None = None

    def __post_init__(self) -> None:
        if self.cost_seconds < 0:
            raise ValueError(f"{self.name}: cost cannot be negative")
        if self.source_map_id == self.target_map_id:
            raise ValueError(f"{self.name}: edge loops back to its own zone")

    def is_available_to(
        self,
        *,
        faction: str | None = None,
        character_class: str | None = None,
        professions: dict[str, int] | None = None,
        level: int | None = None,
    ) -> bool:
        """Whether a character meeting this description may use the edge.

        Requirements must be *positively satisfied*. An unknown capability
        counts as absent, not as a wildcard — the earlier reading let a
        caller who simply did not mention a class route a warrior through
        a mage teleport. Silently over-permitting here produces routes the
        player cannot walk, which is worse than an overly cautious one.
        """
        req = self.requirement
        professions = professions or {}
        have = (
            professions.get(req.profession.lower(), 0)
            if req.profession is not None
            else 0
        )
        # Unspecified `faction`/`character_class` must NOT satisfy a
        # requirement: `None != "mage"` is True, so an unstated class fails a
        # mage-only edge rather than passing it. Guarding with `is not None`
        # would revive the wildcard bug the tests guard against.
        return not (
            (req.faction is not None and faction != req.faction)
            or (
                req.character_class is not None
                and character_class != req.character_class
            )
            or (req.profession is not None and have == 0)
            or (
                req.profession_skill is not None
                and have < req.profession_skill
            )
            or (req.min_level is not None and (level is None or level < req.min_level))
        )


# ---------------------------------------------------------------------------
# Cost constants
#
# Calibrated against typical play at current-expansion flight speeds. Named
# rather than inlined so the whole model can be retuned in one place when
# Blizzard changes travel speeds.
# ---------------------------------------------------------------------------

PORTAL_COST = 20.0  # click, load screen, arrive
MAGE_PORTAL_COST = 15.0  # cast plus load
TELEPORT_COST = 25.0  # cast time on a self-teleport
HEARTHSTONE_COST = 30.0  # 10s cast plus load
ENGINEERING_COST = 35.0  # longer cast, non-trivial failure chance
FLIGHT_SHORT = 90.0
FLIGHT_MEDIUM = 180.0
FLIGHT_LONG = 300.0
BOAT_COST = 150.0  # includes average wait for the schedule
ZEPPELIN_COST = 150.0
TRAM_COST = 75.0
WALK_ADJACENT = 60.0

# Major hubs referenced repeatedly below.
STORMWIND = 1519
IRONFORGE = 1537
ORGRIMMAR = 1947
THUNDER_BLUFF = 1943
UNDERCITY = 1954
DARNASSUS = 1657
SHATTRATH = 111
DALARAN_NORTHREND = 125
DALARAN_LEGION = 627
VALE = 390
ORIBOS = 1670
VALDRAKKEN = 2112
DORNOGAL = 2339
BORALUS = 1161
DAZARALOR = 1165
EMERALD_DREAM = 2200


def _edge(
    source: int,
    target: int,
    method: TravelMethod,
    cost: float,
    name: str,
    requirement: TravelRequirement | None = None,
) -> TravelEdge:
    return TravelEdge(
        source_map_id=source,
        target_map_id=target,
        method=method,
        cost_seconds=cost,
        name=name,
        requirement=requirement or TravelRequirement(),
    )


def _portal_pair(
    a: int, b: int, name_ab: str, name_ba: str, *, faction: str | None = None
) -> list[TravelEdge]:
    """Two directed edges for a genuinely bidirectional portal."""
    req = TravelRequirement(faction=faction)
    return [
        _edge(a, b, TravelMethod.PORTAL, PORTAL_COST, name_ab, req),
        _edge(b, a, TravelMethod.PORTAL, PORTAL_COST, name_ba, req),
    ]


_ALLIANCE = TravelRequirement(faction="alliance")
_HORDE = TravelRequirement(faction="horde")

_CORE_EDGES: list[TravelEdge] = []

# ------------------------------------------------------- Capital city links
_CORE_EDGES += _portal_pair(
    STORMWIND, IRONFORGE, "Deeprun Tram to Ironforge",
    "Deeprun Tram to Stormwind", faction="alliance",
)
_CORE_EDGES += [
    _edge(ORGRIMMAR, THUNDER_BLUFF, TravelMethod.ZEPPELIN, ZEPPELIN_COST,
          "Zeppelin to Thunder Bluff", _HORDE),
    _edge(THUNDER_BLUFF, ORGRIMMAR, TravelMethod.ZEPPELIN, ZEPPELIN_COST,
          "Zeppelin to Orgrimmar", _HORDE),
    _edge(ORGRIMMAR, UNDERCITY, TravelMethod.ZEPPELIN, ZEPPELIN_COST,
          "Zeppelin to Undercity", _HORDE),
    _edge(UNDERCITY, ORGRIMMAR, TravelMethod.ZEPPELIN, ZEPPELIN_COST,
          "Zeppelin to Orgrimmar", _HORDE),
    _edge(STORMWIND, DARNASSUS, TravelMethod.BOAT, BOAT_COST,
          "Boat to Darnassus", _ALLIANCE),
    _edge(DARNASSUS, STORMWIND, TravelMethod.BOAT, BOAT_COST,
          "Boat to Stormwind", _ALLIANCE),
]

# ------------------------------------------- Capital → expansion hub portals
# Both capitals have a portal room reaching each expansion's hub. These are
# the backbone of nearly every cross-expansion route.
for _capital, _faction in ((STORMWIND, "alliance"), (ORGRIMMAR, "horde")):
    for _hub, _hub_name in (
        (SHATTRATH, "Shattrath City"),
        (DALARAN_NORTHREND, "Dalaran (Northrend)"),
        (VALE, "Vale of Eternal Blossoms"),
        (DALARAN_LEGION, "Dalaran (Broken Isles)"),
        (ORIBOS, "Oribos"),
        (VALDRAKKEN, "Valdrakken"),
        (DORNOGAL, "Dornogal"),
    ):
        _req = TravelRequirement(faction=_faction)
        _CORE_EDGES.append(
            _edge(_capital, _hub, TravelMethod.PORTAL, PORTAL_COST,
                  f"Portal to {_hub_name}", _req)
        )
        _CORE_EDGES.append(
            _edge(_hub, _capital, TravelMethod.PORTAL, PORTAL_COST,
                  "Portal to " + ("Stormwind" if _capital == STORMWIND
                                  else "Orgrimmar"), _req)
        )

# Battle for Azeroth hubs are faction-exclusive.
_CORE_EDGES += [
    _edge(STORMWIND, BORALUS, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Boralus", _ALLIANCE),
    _edge(BORALUS, STORMWIND, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Stormwind", _ALLIANCE),
    _edge(ORGRIMMAR, DAZARALOR, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Dazar'alor", _HORDE),
    _edge(DAZARALOR, ORGRIMMAR, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Orgrimmar", _HORDE),
]

# -------------------------------------------------------- Hub → zone portals
_CORE_EDGES += [
    # Shattrath is the Outland wheel.
    _edge(SHATTRATH, 100, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Hellfire Peninsula"),
    _edge(SHATTRATH, 107, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Nagrand"),
    _edge(SHATTRATH, 108, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Terokkar Forest"),
    _edge(SHATTRATH, 102, TravelMethod.FLIGHT_PATH, FLIGHT_MEDIUM,
          "Flight to Zangarmarsh"),
    # Legion: Dalaran reaches the Broken Isles.
    _edge(DALARAN_LEGION, 630, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Azsuna"),
    _edge(DALARAN_LEGION, 634, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Stormheim"),
    _edge(DALARAN_LEGION, 641, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Val'sharah"),
    _edge(DALARAN_LEGION, 650, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Highmountain"),
    _edge(DALARAN_LEGION, 680, TravelMethod.FLIGHT_PATH, FLIGHT_MEDIUM,
          "Flight to Suramar"),
    _edge(DALARAN_LEGION, 646, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to the Broken Shore"),
    # Shadowlands: Oribos reaches each covenant zone.
    _edge(ORIBOS, 1533, TravelMethod.PORTAL, PORTAL_COST, "Portal to Bastion"),
    _edge(ORIBOS, 1536, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Maldraxxus"),
    _edge(ORIBOS, 1565, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Ardenweald"),
    _edge(ORIBOS, 1525, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Revendreth"),
    _edge(ORIBOS, 1970, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Zereth Mortis"),
    _edge(1533, ORIBOS, TravelMethod.PORTAL, PORTAL_COST, "Portal to Oribos"),
    _edge(1536, ORIBOS, TravelMethod.PORTAL, PORTAL_COST, "Portal to Oribos"),
    _edge(1565, ORIBOS, TravelMethod.PORTAL, PORTAL_COST, "Portal to Oribos"),
    _edge(1525, ORIBOS, TravelMethod.PORTAL, PORTAL_COST, "Portal to Oribos"),
    # Dragonflight: Valdrakken sits central to the Dragon Isles.
    _edge(VALDRAKKEN, 2022, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to The Waking Shores"),
    _edge(VALDRAKKEN, 2023, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Ohn'ahran Plains"),
    _edge(VALDRAKKEN, 2024, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to The Azure Span"),
    _edge(VALDRAKKEN, 2025, TravelMethod.WALK, WALK_ADJACENT,
          "Walk into Thaldraszus"),
    _edge(2025, VALDRAKKEN, TravelMethod.WALK, WALK_ADJACENT,
          "Walk into Valdrakken"),
    _edge(VALDRAKKEN, 2133, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Zaralek Cavern"),
    _edge(VALDRAKKEN, EMERALD_DREAM, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to the Emerald Dream"),
    # The War Within: Dornogal serves Khaz Algar.
    _edge(DORNOGAL, 2248, TravelMethod.WALK, WALK_ADJACENT,
          "Walk into the Isle of Dorn"),
    _edge(2248, DORNOGAL, TravelMethod.WALK, WALK_ADJACENT,
          "Walk into Dornogal"),
    _edge(DORNOGAL, 2214, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to The Ringing Deeps"),
    _edge(DORNOGAL, 2215, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Hallowfall"),
    _edge(DORNOGAL, 2255, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Azj-Kahet"),
    _edge(DORNOGAL, 2346, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Undermine"),
    _edge(DORNOGAL, 2371, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to K'aresh"),
    _edge(2214, DORNOGAL, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Dornogal"),
    _edge(2215, DORNOGAL, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Dornogal"),
    _edge(2255, DORNOGAL, TravelMethod.PORTAL, PORTAL_COST,
          "Portal to Dornogal"),
    # Battle for Azeroth zone links.
    _edge(BORALUS, 895, TravelMethod.WALK, WALK_ADJACENT,
          "Walk into Tiragarde Sound"),
    _edge(BORALUS, 896, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Drustvar"),
    _edge(BORALUS, 942, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Stormsong Valley"),
    _edge(DAZARALOR, 862, TravelMethod.WALK, WALK_ADJACENT,
          "Walk into Zuldazar"),
    _edge(DAZARALOR, 863, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Nazmir"),
    _edge(DAZARALOR, 864, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Vol'dun"),
    # Pandaria.
    _edge(VALE, 371, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to The Jade Forest"),
    _edge(VALE, 376, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Valley of the Four Winds"),
    _edge(VALE, 379, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Kun-Lai Summit"),
    # Northrend.
    _edge(DALARAN_NORTHREND, 114, TravelMethod.FLIGHT_PATH, FLIGHT_MEDIUM,
          "Flight to Borean Tundra"),
    _edge(DALARAN_NORTHREND, 115, TravelMethod.FLIGHT_PATH, FLIGHT_SHORT,
          "Flight to Dragonblight"),
    _edge(DALARAN_NORTHREND, 118, TravelMethod.FLIGHT_PATH, FLIGHT_MEDIUM,
          "Flight to Icecrown"),
    _edge(DALARAN_NORTHREND, 120, TravelMethod.FLIGHT_PATH, FLIGHT_MEDIUM,
          "Flight to The Storm Peaks"),
]

# --------------------------------------------------------- Mage portal spells
# Mages reach every capital and current hub directly. These are one-way: the
# spell takes you there, it does not bring you back.
_MAGE_DESTINATIONS: tuple[tuple[int, str, str | None], ...] = (
    (STORMWIND, "Teleport: Stormwind", "alliance"),
    (IRONFORGE, "Teleport: Ironforge", "alliance"),
    (DARNASSUS, "Teleport: Darnassus", "alliance"),
    (BORALUS, "Teleport: Boralus", "alliance"),
    (ORGRIMMAR, "Teleport: Orgrimmar", "horde"),
    (THUNDER_BLUFF, "Teleport: Thunder Bluff", "horde"),
    (UNDERCITY, "Teleport: Undercity", "horde"),
    (DAZARALOR, "Teleport: Dazar'alor", "horde"),
    (SHATTRATH, "Teleport: Shattrath", None),
    (DALARAN_NORTHREND, "Teleport: Dalaran - Northrend", None),
    (VALE, "Teleport: Vale of Eternal Blossoms", None),
    (DALARAN_LEGION, "Teleport: Dalaran - Broken Isles", None),
    (ORIBOS, "Teleport: Oribos", None),
    (VALDRAKKEN, "Teleport: Valdrakken", None),
    (DORNOGAL, "Teleport: Dornogal", None),
)


def _mage_edges() -> list[TravelEdge]:
    """A mage teleport from any hub to any mage destination.

    Mage teleports are castable anywhere, but enumerating an edge from every
    one of 112 zones would add thousands of edges for no routing benefit —
    the planner only ever needs to leave from where the character is. So
    edges originate from hubs, and :func:`app.navigation.router` injects an
    origin-specific set for the actual starting zone at query time.
    """
    edges: list[TravelEdge] = []
    origins = {
        STORMWIND, ORGRIMMAR, SHATTRATH, DALARAN_NORTHREND, VALE,
        DALARAN_LEGION, ORIBOS, VALDRAKKEN, DORNOGAL, BORALUS, DAZARALOR,
    }
    for origin in origins:
        for target, spell, faction in _MAGE_DESTINATIONS:
            if origin == target:
                continue
            edges.append(
                _edge(
                    origin, target, TravelMethod.MAGE_PORTAL,
                    MAGE_PORTAL_COST, spell,
                    TravelRequirement(
                        character_class="mage", faction=faction, min_level=10
                    ),
                )
            )
    return edges


_CORE_EDGES += _mage_edges()

# ------------------------------------------------------ Druid Dreamwalk lines
# Dreamwalk reaches the Emerald Dream, which in turn has portals out to each
# Dragon Isles zone — making it a genuinely competitive route for druids.
_DRUID = TravelRequirement(
    character_class="druid", min_level=10, cooldown_seconds=600,
    notes=("Dreamwalk",),
)
_CORE_EDGES += [
    _edge(origin, EMERALD_DREAM, TravelMethod.DREAMWALK, TELEPORT_COST,
          "Dreamwalk to the Emerald Dream", _DRUID)
    for origin in (
        STORMWIND, ORGRIMMAR, VALDRAKKEN, DORNOGAL, ORIBOS, DALARAN_LEGION,
    )
]
_CORE_EDGES += [
    _edge(EMERALD_DREAM, target, TravelMethod.PORTAL, PORTAL_COST,
          f"Emerald Dream portal to {name}")
    for target, name in (
        (2022, "The Waking Shores"), (2023, "Ohn'ahran Plains"),
        (2024, "The Azure Span"), (2025, "Thaldraszus"),
        (VALDRAKKEN, "Valdrakken"),
    )
]

# ------------------------------------------------------ Engineering teleports
# Wormholes and toys, gated on Engineering skill. Notoriously prone to
# misfiring, which the higher cost reflects.
_ENGI = TravelRequirement(
    profession="engineering", profession_skill=1, min_level=10,
    notes=("Engineering wormhole",),
)
_CORE_EDGES += [
    _edge(origin, target, TravelMethod.ENGINEERING_TELEPORT, ENGINEERING_COST,
          name, _ENGI)
    for origin, target, name in (
        (DALARAN_NORTHREND, 114, "Wormhole Generator: Northrend"),
        (VALE, 371, "Wormhole Generator: Pandaria"),
        (DALARAN_LEGION, 630, "Wormhole Generator: Broken Isles"),
        (BORALUS, 895, "Wormhole Generator: Kul Tiras"),
        (DAZARALOR, 862, "Wormhole Generator: Zandalar"),
        (ORIBOS, 1533, "Wormhole Generator: Shadowlands"),
        (VALDRAKKEN, 2022, "Wormhole Generator: Dragon Isles"),
        (DORNOGAL, 2248, "Wormhole Generator: Khaz Algar"),
    )
]

#: The live network. Extend with :func:`register_edges`.
TRAVEL_EDGES: list[TravelEdge] = list(_CORE_EDGES)


def register_edges(edges: Iterable[TravelEdge]) -> None:
    """Add travel options to the network."""
    TRAVEL_EDGES.extend(edges)


def edges_from(map_id: int) -> list[TravelEdge]:
    """Every outbound edge from a zone."""
    return [e for e in TRAVEL_EDGES if e.source_map_id == map_id]


def validate_network() -> list[str]:
    """Return integrity problems; empty means healthy."""
    from app.world.zones import ZONES

    problems: list[str] = []
    seen: set[tuple[int, int, str, str]] = set()

    for edge in TRAVEL_EDGES:
        if edge.source_map_id not in ZONES:
            problems.append(
                f"{edge.name}: source map {edge.source_map_id} is not a "
                "registered zone"
            )
        if edge.target_map_id not in ZONES:
            problems.append(
                f"{edge.name}: target map {edge.target_map_id} is not a "
                "registered zone"
            )
        key = (edge.source_map_id, edge.target_map_id, edge.method, edge.name)
        if key in seen:
            problems.append(f"Duplicate edge: {edge.name} ({key[0]}->{key[1]})")
        seen.add(key)

        # A faction-locked edge into a hub of the other faction is a data
        # error that would route players into a city they cannot enter.
        target = ZONES.get(edge.target_map_id)
        if (
            target is not None
            and target.faction is not None
            and edge.requirement.faction is not None
            and target.faction != edge.requirement.faction
        ):
            problems.append(
                f"{edge.name}: {edge.requirement.faction} edge targets "
                f"{target.faction} zone {target.name}"
            )

    return problems
