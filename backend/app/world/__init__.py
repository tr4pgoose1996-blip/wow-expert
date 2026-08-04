"""Shared world model: geography and the zone registry.

Both the quest and navigation systems depend on this layer; neither depends
on the other.
"""

from app.world.geography import Expansion, MapPoint, Zone, ZoneConnectionKind
from app.world.zones import (
    ZONES,
    all_zones,
    get_zone,
    register_zones,
    validate_registry,
    zones_for_expansion,
    zones_for_level,
)

__all__ = [
    "ZONES",
    "Expansion",
    "MapPoint",
    "Zone",
    "ZoneConnectionKind",
    "all_zones",
    "get_zone",
    "register_zones",
    "validate_registry",
    "zones_for_expansion",
    "zones_for_level",
]
