"""Tests for the shared world model: geography and the zone registry."""

from __future__ import annotations

import pytest

from app.world.geography import Expansion, MapPoint, Zone
from app.world.zones import (
    ZONES,
    get_zone,
    register_zones,
    validate_registry,
    zones_for_expansion,
    zones_for_level,
)


class TestExpansion:
    def test_ordering_follows_release(self) -> None:
        assert Expansion.CLASSIC < Expansion.WAR_WITHIN
        assert Expansion.DRAGONFLIGHT < Expansion.WAR_WITHIN

    def test_every_expansion_has_a_display_name_and_levels(self) -> None:
        for expansion in Expansion:
            assert expansion.display_name
            low, high = expansion.level_range
            assert 1 <= low <= high <= 90

    def test_order_is_unique(self) -> None:
        orders = [e.order for e in Expansion]
        assert len(set(orders)) == len(orders)


class TestMapPoint:
    def test_percent_round_trip(self) -> None:
        point = MapPoint.from_percent(2248, 46.8, 61.2)
        assert point.as_percent == (46.8, 61.2)

    def test_normalised_storage(self) -> None:
        point = MapPoint.from_percent(2248, 50.0, 25.0)
        assert point.x == pytest.approx(0.5)
        assert point.y == pytest.approx(0.25)

    def test_rejects_out_of_range(self) -> None:
        """A raw percentage passed as a fraction is the likely mistake."""
        with pytest.raises(ValueError, match="normalised"):
            MapPoint(map_id=2248, x=46.8, y=61.2)

    def test_rejects_bad_map_id(self) -> None:
        with pytest.raises(ValueError, match="map_id"):
            MapPoint(map_id=0, x=0.5, y=0.5)

    def test_distance_within_zone(self) -> None:
        a = MapPoint.from_percent(2248, 0.0, 0.0)
        b = MapPoint.from_percent(2248, 30.0, 40.0)
        assert a.planar_distance(b) == pytest.approx(0.5)

    def test_cross_zone_distance_is_refused(self) -> None:
        """Silently returning a number here would be confident nonsense."""
        a = MapPoint.from_percent(2248, 50.0, 50.0)
        b = MapPoint.from_percent(2339, 50.0, 50.0)
        with pytest.raises(ValueError, match="travel graph"):
            a.planar_distance(b)


class TestZoneRegistry:
    def test_registry_is_internally_consistent(self) -> None:
        assert validate_registry() == []

    def test_every_shipped_expansion_has_zones(self) -> None:
        for expansion in Expansion:
            if expansion is Expansion.MIDNIGHT:
                continue  # unreleased; data pack lands with the expansion
            assert zones_for_expansion(expansion), f"{expansion} has no zones"

    def test_known_map_ids_resolve(self) -> None:
        """Spot-check ids that TomTom output depends on being right."""
        expected = {
            1519: "Stormwind City",
            1947: "Orgrimmar",
            2339: "Dornogal",
            2112: "Valdrakken",
            1670: "Oribos",
        }
        for map_id, name in expected.items():
            zone = get_zone(map_id)
            assert zone is not None and zone.name == name

    def test_lookup_of_unknown_zone(self) -> None:
        assert get_zone(999_999) is None

    def test_zones_for_level_respects_bands(self) -> None:
        for zone in zones_for_level(72):
            assert zone.level_range is not None
            assert zone.level_range[0] <= 72 <= zone.level_range[1]

    def test_cities_are_marked(self) -> None:
        assert get_zone(1519).is_city
        assert not get_zone(2248).is_city

    def test_the_maw_is_not_flyable(self) -> None:
        assert get_zone(1543).flyable is False

    def test_registering_a_new_zone_works(self) -> None:
        zone = Zone(
            map_id=999_001,
            name="Test Zone",
            expansion=Expansion.MIDNIGHT,
            continent="Test",
        )
        try:
            register_zones([zone])
            assert get_zone(999_001) == zone
        finally:
            ZONES.pop(999_001, None)

    def test_conflicting_redefinition_is_refused(self) -> None:
        """A silent overwrite would let two definitions disagree."""
        clash = Zone(
            map_id=1519,
            name="Not Stormwind",
            expansion=Expansion.CLASSIC,
            continent="Eastern Kingdoms",
        )
        with pytest.raises(ValueError, match="already registered"):
            register_zones([clash])

    def test_reregistering_identical_zone_is_allowed(self) -> None:
        """Idempotent imports must not explode."""
        register_zones([get_zone(1519)])
