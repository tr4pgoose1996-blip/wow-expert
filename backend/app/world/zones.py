"""Zone registry: real ``uiMapID`` values across every expansion.

Map IDs are the client's own identifiers. They are correct as of the current
retail build and are what TomTom's ``/way #<mapID>`` form consumes, so a
wrong value here produces a waypoint that silently lands in the wrong zone —
hence :func:`validate_registry`, which runs in the test suite.

This is a curated core, not an exhaustive dump: the major questing zones and
hubs of each expansion, which is what routing and recommendations need. The
registry is additive — :func:`register_zones` lets a data pack extend it
without editing this file.
"""

from __future__ import annotations

from app.world.geography import Expansion, Zone

__all__ = [
    "ZONES",
    "all_zones",
    "get_zone",
    "register_zones",
    "validate_registry",
    "zones_for_expansion",
    "zones_for_level",
]


def _z(
    map_id: int,
    name: str,
    expansion: Expansion,
    continent: str,
    level_range: tuple[int, int] | None = None,
    *,
    faction: str | None = None,
    is_city: bool = False,
    flyable: bool = True,
) -> Zone:
    return Zone(
        map_id=map_id,
        name=name,
        expansion=expansion,
        continent=continent,
        level_range=level_range,
        faction=faction,
        is_city=is_city,
        flyable=flyable,
    )


_CORE_ZONES: tuple[Zone, ...] = (
    # ---------------------------------------------------------------- Classic
    _z(1519, "Stormwind City", Expansion.CLASSIC, "Eastern Kingdoms",
       faction="alliance", is_city=True),
    _z(1537, "Ironforge", Expansion.CLASSIC, "Eastern Kingdoms",
       faction="alliance", is_city=True),
    _z(1947, "Orgrimmar", Expansion.CLASSIC, "Kalimdor",
       faction="horde", is_city=True),
    _z(1943, "Thunder Bluff", Expansion.CLASSIC, "Kalimdor",
       faction="horde", is_city=True),
    _z(1954, "Undercity", Expansion.CLASSIC, "Eastern Kingdoms",
       faction="horde", is_city=True),
    _z(1657, "Darnassus", Expansion.CLASSIC, "Kalimdor",
       faction="alliance", is_city=True),
    _z(37, "Elwynn Forest", Expansion.CLASSIC, "Eastern Kingdoms", (1, 10)),
    _z(10, "Northern Barrens", Expansion.CLASSIC, "Kalimdor", (10, 20)),
    _z(47, "Duskwood", Expansion.CLASSIC, "Eastern Kingdoms", (10, 30)),
    _z(51, "Searing Gorge", Expansion.CLASSIC, "Eastern Kingdoms", (10, 30)),
    _z(1417, "Northern Stranglethorn", Expansion.CLASSIC,
       "Eastern Kingdoms", (10, 30)),
    _z(15, "Badlands", Expansion.CLASSIC, "Eastern Kingdoms", (10, 30)),
    _z(22, "Western Plaguelands", Expansion.CLASSIC,
       "Eastern Kingdoms", (10, 30)),
    _z(23, "Eastern Plaguelands", Expansion.CLASSIC,
       "Eastern Kingdoms", (10, 30)),
    _z(56, "Wetlands", Expansion.CLASSIC, "Eastern Kingdoms", (10, 30)),
    _z(14, "Arathi Highlands", Expansion.CLASSIC,
       "Eastern Kingdoms", (10, 30)),
    _z(17, "Blasted Lands", Expansion.CLASSIC, "Eastern Kingdoms", (10, 30)),
    _z(71, "Tanaris", Expansion.CLASSIC, "Kalimdor", (10, 30)),
    _z(70, "Dustwallow Marsh", Expansion.CLASSIC, "Kalimdor", (10, 30)),
    _z(64, "Thousand Needles", Expansion.CLASSIC, "Kalimdor", (10, 30)),
    _z(69, "Feralas", Expansion.CLASSIC, "Kalimdor", (10, 30)),
    _z(78, "Silithus", Expansion.CLASSIC, "Kalimdor", (10, 30)),
    _z(1527, "Un'Goro Crater", Expansion.CLASSIC, "Kalimdor", (10, 30)),
    _z(83, "Winterspring", Expansion.CLASSIC, "Kalimdor", (10, 30)),
    _z(2070, "Elwynn Forest (Midnight)", Expansion.CLASSIC,
       "Eastern Kingdoms", (1, 10)),

    # -------------------------------------------------------- Burning Crusade
    _z(111, "Shattrath City", Expansion.BURNING_CRUSADE, "Outland",
       is_city=True),
    _z(100, "Hellfire Peninsula", Expansion.BURNING_CRUSADE,
       "Outland", (10, 30)),
    _z(102, "Zangarmarsh", Expansion.BURNING_CRUSADE, "Outland", (10, 30)),
    _z(104, "Shadowmoon Valley", Expansion.BURNING_CRUSADE,
       "Outland", (10, 30)),
    _z(105, "Blade's Edge Mountains", Expansion.BURNING_CRUSADE,
       "Outland", (10, 30)),
    _z(106, "Netherstorm", Expansion.BURNING_CRUSADE, "Outland", (10, 30)),
    _z(107, "Nagrand", Expansion.BURNING_CRUSADE, "Outland", (10, 30)),
    _z(108, "Terokkar Forest", Expansion.BURNING_CRUSADE,
       "Outland", (10, 30)),
    _z(109, "Isle of Quel'Danas", Expansion.BURNING_CRUSADE,
       "Eastern Kingdoms", (10, 30)),

    # ------------------------------------------------------------------ Wrath
    _z(125, "Dalaran", Expansion.WRATH, "Northrend", is_city=True),
    _z(114, "Borean Tundra", Expansion.WRATH, "Northrend", (10, 30)),
    _z(115, "Dragonblight", Expansion.WRATH, "Northrend", (10, 30)),
    _z(116, "Grizzly Hills", Expansion.WRATH, "Northrend", (10, 30)),
    _z(117, "Howling Fjord", Expansion.WRATH, "Northrend", (10, 30)),
    _z(118, "Icecrown", Expansion.WRATH, "Northrend", (10, 30)),
    _z(119, "Sholazar Basin", Expansion.WRATH, "Northrend", (10, 30)),
    _z(120, "The Storm Peaks", Expansion.WRATH, "Northrend", (10, 30)),
    _z(121, "Zul'Drak", Expansion.WRATH, "Northrend", (10, 30)),
    _z(123, "Wintergrasp", Expansion.WRATH, "Northrend", (10, 30)),

    # ------------------------------------------------------------- Cataclysm
    _z(198, "Mount Hyjal", Expansion.CATACLYSM, "Kalimdor", (10, 30)),
    _z(201, "Kelp'thar Forest", Expansion.CATACLYSM, "Vashj'ir", (10, 30)),
    _z(203, "Vashj'ir", Expansion.CATACLYSM, "Vashj'ir", (10, 30)),
    _z(207, "Deepholm", Expansion.CATACLYSM, "Deepholm", (10, 30)),
    _z(241, "Twilight Highlands", Expansion.CATACLYSM,
       "Eastern Kingdoms", (10, 30)),
    _z(249, "Uldum", Expansion.CATACLYSM, "Kalimdor", (10, 30)),

    # -------------------------------------------------------------- Pandaria
    _z(390, "Vale of Eternal Blossoms", Expansion.PANDARIA,
       "Pandaria", (10, 35)),
    _z(371, "The Jade Forest", Expansion.PANDARIA, "Pandaria", (10, 35)),
    _z(376, "Valley of the Four Winds", Expansion.PANDARIA,
       "Pandaria", (10, 35)),
    _z(379, "Kun-Lai Summit", Expansion.PANDARIA, "Pandaria", (10, 35)),
    _z(388, "Townlong Steppes", Expansion.PANDARIA, "Pandaria", (10, 35)),
    _z(422, "Dread Wastes", Expansion.PANDARIA, "Pandaria", (10, 35)),
    _z(418, "Krasarang Wilds", Expansion.PANDARIA, "Pandaria", (10, 35)),
    _z(504, "Isle of Thunder", Expansion.PANDARIA, "Pandaria", (10, 35)),
    _z(554, "Timeless Isle", Expansion.PANDARIA, "Pandaria", (10, 35)),

    # --------------------------------------------------------------- Draenor
    _z(582, "Lunarfall", Expansion.DRAENOR, "Draenor",
       faction="alliance", is_city=True),
    _z(590, "Frostwall", Expansion.DRAENOR, "Draenor",
       faction="horde", is_city=True),
    _z(525, "Frostfire Ridge", Expansion.DRAENOR, "Draenor", (10, 40)),
    _z(535, "Talador", Expansion.DRAENOR, "Draenor", (10, 40)),
    _z(539, "Shadowmoon Valley (Draenor)", Expansion.DRAENOR,
       "Draenor", (10, 40)),
    _z(542, "Spires of Arak", Expansion.DRAENOR, "Draenor", (10, 40)),
    _z(543, "Gorgrond", Expansion.DRAENOR, "Draenor", (10, 40)),
    _z(550, "Nagrand (Draenor)", Expansion.DRAENOR, "Draenor", (10, 40)),
    _z(588, "Ashran", Expansion.DRAENOR, "Draenor", (10, 40)),
    _z(534, "Tanaan Jungle", Expansion.DRAENOR, "Draenor", (10, 40)),

    # ----------------------------------------------------------------- Legion
    _z(627, "Dalaran (Broken Isles)", Expansion.LEGION,
       "Broken Isles", is_city=True),
    _z(630, "Azsuna", Expansion.LEGION, "Broken Isles", (10, 45)),
    _z(634, "Stormheim", Expansion.LEGION, "Broken Isles", (10, 45)),
    _z(641, "Val'sharah", Expansion.LEGION, "Broken Isles", (10, 45)),
    _z(650, "Highmountain", Expansion.LEGION, "Broken Isles", (10, 45)),
    _z(680, "Suramar", Expansion.LEGION, "Broken Isles", (10, 45)),
    _z(646, "Broken Shore", Expansion.LEGION, "Broken Isles", (10, 45)),
    _z(830, "Krokuun", Expansion.LEGION, "Argus", (10, 45)),
    _z(882, "Eredath", Expansion.LEGION, "Argus", (10, 45)),
    _z(885, "Antoran Wastes", Expansion.LEGION, "Argus", (10, 45)),

    # ------------------------------------------------- Battle for Azeroth
    _z(1161, "Boralus", Expansion.BATTLE_FOR_AZEROTH, "Kul Tiras",
       faction="alliance", is_city=True),
    _z(1165, "Dazar'alor", Expansion.BATTLE_FOR_AZEROTH, "Zandalar",
       faction="horde", is_city=True),
    _z(895, "Tiragarde Sound", Expansion.BATTLE_FOR_AZEROTH,
       "Kul Tiras", (10, 50)),
    _z(896, "Drustvar", Expansion.BATTLE_FOR_AZEROTH, "Kul Tiras", (10, 50)),
    _z(942, "Stormsong Valley", Expansion.BATTLE_FOR_AZEROTH,
       "Kul Tiras", (10, 50)),
    _z(862, "Zuldazar", Expansion.BATTLE_FOR_AZEROTH, "Zandalar", (10, 50)),
    _z(863, "Nazmir", Expansion.BATTLE_FOR_AZEROTH, "Zandalar", (10, 50)),
    _z(864, "Vol'dun", Expansion.BATTLE_FOR_AZEROTH, "Zandalar", (10, 50)),
    _z(1355, "Nazjatar", Expansion.BATTLE_FOR_AZEROTH, "Nazjatar", (10, 50)),
    _z(1462, "Mechagon Island", Expansion.BATTLE_FOR_AZEROTH,
       "Mechagon", (10, 50)),

    # ----------------------------------------------------------- Shadowlands
    _z(1670, "Oribos", Expansion.SHADOWLANDS, "Shadowlands", is_city=True),
    _z(1533, "Bastion", Expansion.SHADOWLANDS, "Shadowlands", (48, 60)),
    _z(1536, "Maldraxxus", Expansion.SHADOWLANDS, "Shadowlands", (48, 60)),
    _z(1565, "Ardenweald", Expansion.SHADOWLANDS, "Shadowlands", (48, 60)),
    _z(1525, "Revendreth", Expansion.SHADOWLANDS, "Shadowlands", (48, 60)),
    _z(1543, "The Maw", Expansion.SHADOWLANDS, "Shadowlands", (48, 60),
       flyable=False),
    _z(1961, "Korthia", Expansion.SHADOWLANDS, "Shadowlands", (60, 60)),
    _z(1970, "Zereth Mortis", Expansion.SHADOWLANDS,
       "Shadowlands", (60, 60)),

    # --------------------------------------------------------- Dragonflight
    _z(2112, "Valdrakken", Expansion.DRAGONFLIGHT,
       "Dragon Isles", is_city=True),
    _z(2022, "The Waking Shores", Expansion.DRAGONFLIGHT,
       "Dragon Isles", (58, 70)),
    _z(2023, "Ohn'ahran Plains", Expansion.DRAGONFLIGHT,
       "Dragon Isles", (58, 70)),
    _z(2024, "The Azure Span", Expansion.DRAGONFLIGHT,
       "Dragon Isles", (58, 70)),
    _z(2025, "Thaldraszus", Expansion.DRAGONFLIGHT,
       "Dragon Isles", (58, 70)),
    _z(2151, "The Forbidden Reach", Expansion.DRAGONFLIGHT,
       "Dragon Isles", (68, 70)),
    _z(2133, "Zaralek Cavern", Expansion.DRAGONFLIGHT,
       "Dragon Isles", (68, 70)),
    _z(2200, "Emerald Dream", Expansion.DRAGONFLIGHT,
       "Dragon Isles", (68, 70)),

    # -------------------------------------------------------- The War Within
    _z(2339, "Dornogal", Expansion.WAR_WITHIN, "Khaz Algar", is_city=True),
    _z(2248, "Isle of Dorn", Expansion.WAR_WITHIN, "Khaz Algar", (68, 80)),
    _z(2214, "The Ringing Deeps", Expansion.WAR_WITHIN,
       "Khaz Algar", (68, 80)),
    _z(2215, "Hallowfall", Expansion.WAR_WITHIN, "Khaz Algar", (68, 80)),
    _z(2255, "Azj-Kahet", Expansion.WAR_WITHIN, "Khaz Algar", (68, 80)),
    _z(2346, "Undermine", Expansion.WAR_WITHIN, "Khaz Algar", (80, 80)),
    _z(2371, "K'aresh", Expansion.WAR_WITHIN, "K'aresh", (80, 80)),
)

#: Mutable registry, keyed by map id. Extended via :func:`register_zones`.
ZONES: dict[int, Zone] = {zone.map_id: zone for zone in _CORE_ZONES}


def register_zones(zones: list[Zone]) -> None:
    """Add zones to the registry.

    The extension point for expansion data packs. Re-registering an existing
    map id is rejected: a silent overwrite would mean two zone definitions
    disagreeing about the same map, and whichever imported last would win.
    """
    for zone in zones:
        existing = ZONES.get(zone.map_id)
        if existing is not None and existing != zone:
            raise ValueError(
                f"Map id {zone.map_id} is already registered as "
                f"{existing.name!r}; refusing to redefine it as {zone.name!r}."
            )
        ZONES[zone.map_id] = zone


def get_zone(map_id: int) -> Zone | None:
    return ZONES.get(map_id)


def all_zones() -> list[Zone]:
    return list(ZONES.values())


def zones_for_expansion(expansion: Expansion) -> list[Zone]:
    return [z for z in ZONES.values() if z.expansion is expansion]


def zones_for_level(level: int) -> list[Zone]:
    """Zones whose suggested band contains ``level``."""
    return [
        zone
        for zone in ZONES.values()
        if zone.level_range is not None
        and zone.level_range[0] <= level <= zone.level_range[1]
    ]


def validate_registry() -> list[str]:
    """Return a list of integrity problems; empty means healthy.

    Exercised by the test suite. A duplicate name or an out-of-band level
    range usually means a copy-paste slip in the table above, which would
    otherwise surface as a waypoint pointing at the wrong continent.
    """
    problems: list[str] = []

    by_name: dict[str, int] = {}
    for map_id, zone in ZONES.items():
        if map_id != zone.map_id:
            problems.append(
                f"Zone {zone.name!r} keyed as {map_id} but declares "
                f"{zone.map_id}"
            )
        if zone.name in by_name:
            problems.append(
                f"Duplicate zone name {zone.name!r} "
                f"({by_name[zone.name]} and {map_id})"
            )
        by_name[zone.name] = map_id

        if zone.level_range is not None:
            low, high = zone.level_range
            if low > high:
                problems.append(f"{zone.name}: inverted level range {low}-{high}")
            if not (1 <= low <= 90 and 1 <= high <= 90):
                problems.append(
                    f"{zone.name}: level range {low}-{high} outside 1-90"
                )
        if zone.faction not in (None, "alliance", "horde"):
            problems.append(f"{zone.name}: invalid faction {zone.faction!r}")

    return problems
