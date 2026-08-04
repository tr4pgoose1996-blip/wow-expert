"""Tests for Hermes Navigation AI: travel graph, routing, and TomTom."""

from __future__ import annotations

import pytest

from app.navigation.router import (
    TravelContext,
    estimate_intra_zone_seconds,
    find_route,
    find_routes,
)
from app.navigation.tomtom import (
    Waypoint,
    WaypointSet,
    format_way_command,
    parse_way_command,
)
from app.navigation.travel import (
    TravelEdge,
    TravelMethod,
    TravelRequirement,
    edges_from,
    validate_network,
)
from app.world.geography import MapPoint

STORMWIND = 1519
ORGRIMMAR = 1947
DORNOGAL = 2339
AZJ_KAHET = 2255
AZURE_SPAN = 2024
BASTION = 1533


class TestTravelNetwork:
    def test_network_is_internally_consistent(self) -> None:
        assert validate_network() == []

    def test_edges_cannot_loop(self) -> None:
        with pytest.raises(ValueError, match="loops back"):
            TravelEdge(
                source_map_id=STORMWIND,
                target_map_id=STORMWIND,
                method=TravelMethod.PORTAL,
                cost_seconds=10,
                name="Nowhere",
            )

    def test_negative_cost_is_refused(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            TravelEdge(
                source_map_id=STORMWIND,
                target_map_id=ORGRIMMAR,
                method=TravelMethod.PORTAL,
                cost_seconds=-1,
                name="Impossible",
            )

    def test_capital_has_outbound_options(self) -> None:
        assert len(edges_from(STORMWIND)) > 5

    def test_instant_methods_are_flagged(self) -> None:
        assert TravelMethod.PORTAL.is_instant
        assert not TravelMethod.FLIGHT_PATH.is_instant


class TestRequirements:
    """Requirements must be positively satisfied, never assumed."""

    def test_unknown_class_does_not_satisfy_a_class_requirement(self) -> None:
        edge = TravelEdge(
            source_map_id=STORMWIND,
            target_map_id=DORNOGAL,
            method=TravelMethod.MAGE_PORTAL,
            cost_seconds=15,
            name="Teleport: Dornogal",
            requirement=TravelRequirement(character_class="mage"),
        )
        assert not edge.is_available_to()
        assert not edge.is_available_to(character_class="warrior")
        assert edge.is_available_to(character_class="mage")

    def test_missing_profession_blocks(self) -> None:
        edge = TravelEdge(
            source_map_id=STORMWIND,
            target_map_id=DORNOGAL,
            method=TravelMethod.ENGINEERING_TELEPORT,
            cost_seconds=35,
            name="Wormhole",
            requirement=TravelRequirement(
                profession="engineering", profession_skill=50
            ),
        )
        assert not edge.is_available_to()
        assert not edge.is_available_to(professions={"engineering": 10})
        assert edge.is_available_to(professions={"engineering": 75})

    def test_level_requirement_needs_a_known_level(self) -> None:
        edge = TravelEdge(
            source_map_id=STORMWIND,
            target_map_id=DORNOGAL,
            method=TravelMethod.PORTAL,
            cost_seconds=20,
            name="Gated portal",
            requirement=TravelRequirement(min_level=70),
        )
        assert not edge.is_available_to()
        assert not edge.is_available_to(level=60)
        assert edge.is_available_to(level=80)

    def test_requirements_describe_themselves(self) -> None:
        assert TravelRequirement().describe() == "no requirements"
        assert "Mage" in TravelRequirement(character_class="mage").describe()


class TestRouting:
    def test_same_zone_is_trivial(self) -> None:
        route = find_route(STORMWIND, STORMWIND)
        assert route is not None
        assert route.is_trivial
        assert route.total_seconds == 0.0

    def test_finds_a_cross_continent_route(self) -> None:
        route = find_route(
            STORMWIND, AZJ_KAHET, TravelContext(faction="alliance", level=80)
        )
        assert route is not None
        assert route.steps
        assert route.total_seconds > 0

    def test_warrior_is_never_routed_through_a_mage_teleport(self) -> None:
        """The bug this guards: unknown capability read as permission."""
        route = find_route(
            STORMWIND,
            AZJ_KAHET,
            TravelContext(faction="alliance", character_class="warrior", level=80),
        )
        assert route is not None
        for step in route.steps:
            assert step.edge.method is not TravelMethod.MAGE_PORTAL

    def test_mage_route_is_no_slower_than_the_public_one(self) -> None:
        base = find_route(
            STORMWIND,
            AZJ_KAHET,
            TravelContext(faction="alliance", character_class="warrior", level=80),
        )
        mage = find_route(
            STORMWIND,
            AZJ_KAHET,
            TravelContext(faction="alliance", character_class="mage", level=80),
        )
        assert mage is not None and base is not None
        assert mage.total_seconds <= base.total_seconds

    def test_druid_gets_a_dreamwalk_route(self) -> None:
        route = find_route(
            STORMWIND,
            AZURE_SPAN,
            TravelContext(faction="alliance", character_class="druid", level=70),
        )
        assert route is not None
        assert TravelMethod.DREAMWALK in route.methods

    def test_non_druid_gets_no_dreamwalk(self) -> None:
        route = find_route(
            STORMWIND,
            AZURE_SPAN,
            TravelContext(faction="alliance", character_class="warrior", level=70),
        )
        assert route is not None
        assert TravelMethod.DREAMWALK not in route.methods

    def test_faction_gating_is_enforced(self) -> None:
        """An Alliance player must not be routed through Horde portals."""
        route = find_route(
            ORGRIMMAR, BASTION, TravelContext(faction="alliance", level=60)
        )
        if route is not None:
            for step in route.steps:
                assert step.edge.requirement.faction in (None, "alliance")

    def test_excluding_a_method_changes_the_route(self) -> None:
        with_portals = find_route(
            STORMWIND, BASTION, TravelContext(faction="alliance", level=60)
        )
        without = find_route(
            STORMWIND,
            BASTION,
            TravelContext(
                faction="alliance",
                level=60,
                excluded_methods=frozenset({TravelMethod.PORTAL}),
            ),
        )
        assert with_portals is not None
        if without is not None:
            assert without.total_seconds >= with_portals.total_seconds
            assert TravelMethod.PORTAL not in without.methods

    def test_hearthstone_is_offered_when_bound(self) -> None:
        route = find_route(
            AZJ_KAHET,
            STORMWIND,
            TravelContext(
                faction="alliance", level=80, hearthstone_map_id=STORMWIND
            ),
        )
        assert route is not None

    def test_unreachable_returns_none_not_a_fake_route(self) -> None:
        assert find_route(STORMWIND, 999_999) is None

    def test_route_serialises(self) -> None:
        route = find_route(
            STORMWIND, DORNOGAL, TravelContext(faction="alliance", level=80)
        )
        payload = route.as_dict()
        assert payload["step_count"] == len(route.steps)
        assert payload["total_seconds"] > 0
        assert all("requirement" in s for s in payload["steps"])


class TestAlternativeRoutes:
    def test_returns_distinct_alternatives(self) -> None:
        routes = find_routes(
            STORMWIND,
            AZURE_SPAN,
            TravelContext(faction="alliance", level=70),
            limit=3,
        )
        assert routes
        signatures = {tuple(s.edge.name for s in r.steps) for r in routes}
        assert len(signatures) == len(routes), "alternatives must differ"

    def test_sorted_cheapest_first(self) -> None:
        routes = find_routes(
            STORMWIND,
            AZURE_SPAN,
            TravelContext(faction="alliance", level=70),
            limit=3,
        )
        costs = [r.total_seconds for r in routes]
        assert costs == sorted(costs)

    def test_limit_is_respected(self) -> None:
        routes = find_routes(
            STORMWIND, AZURE_SPAN, TravelContext(faction="alliance"), limit=2
        )
        assert len(routes) <= 2

    def test_invalid_limit_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            find_routes(STORMWIND, DORNOGAL, limit=0)


class TestIntraZoneEstimate:
    def test_scales_with_distance(self) -> None:
        origin = MapPoint.from_percent(2248, 10.0, 10.0)
        near = MapPoint.from_percent(2248, 15.0, 10.0)
        far = MapPoint.from_percent(2248, 80.0, 80.0)
        assert estimate_intra_zone_seconds(origin, near) < estimate_intra_zone_seconds(
            origin, far
        )

    def test_ground_travel_is_slower(self) -> None:
        a = MapPoint.from_percent(1543, 10.0, 10.0)
        b = MapPoint.from_percent(1543, 60.0, 60.0)
        assert estimate_intra_zone_seconds(
            a, b, flyable=False
        ) > estimate_intra_zone_seconds(a, b, flyable=True)


class TestTomTom:
    def test_command_matches_addon_syntax(self) -> None:
        """Verified against TomTom v4.3.8: /way #<uiMapID> <x> <y> <desc>."""
        point = MapPoint.from_percent(2339, 61.5, 18.5)
        assert format_way_command(point, "Seam Ripper") == (
            "/way #2339 61.50 18.50 Seam Ripper"
        )

    def test_command_without_description(self) -> None:
        point = MapPoint.from_percent(2248, 50.0, 50.0)
        assert format_way_command(point) == "/way #2248 50.00 50.00"

    def test_newlines_cannot_break_out_of_the_command(self) -> None:
        """A newline would split one waypoint into a stray chat message."""
        point = MapPoint.from_percent(2248, 50.0, 50.0)
        command = format_way_command(point, "evil\n/say hacked")
        assert "\n" not in command
        assert command.count("/way") == 1

    def test_pipe_characters_are_stripped(self) -> None:
        point = MapPoint.from_percent(2248, 50.0, 50.0)
        assert "|" not in format_way_command(point, "a|cffff0000red")

    def test_long_description_is_truncated(self) -> None:
        point = MapPoint.from_percent(2248, 50.0, 50.0)
        command = format_way_command(point, "x" * 500)
        assert len(command) < 255

    def test_round_trip(self) -> None:
        original = MapPoint.from_percent(2255, 33.33, 66.67)
        parsed = parse_way_command(format_way_command(original, "Test"))
        assert parsed is not None
        assert parsed.point.map_id == 2255
        assert parsed.point.as_percent == (33.33, 66.67)
        assert parsed.description == "Test"

    def test_parsing_rejects_garbage(self) -> None:
        assert parse_way_command("hello world") is None
        assert parse_way_command("/way Nagrand 45 50") is None

    def test_parsing_rejects_out_of_range_coordinates(self) -> None:
        assert parse_way_command("/way #2248 150.0 50.0") is None

    def test_macro_includes_reset_by_default(self) -> None:
        waypoints = WaypointSet(
            waypoints=(
                Waypoint(MapPoint.from_percent(2248, 10.0, 20.0), "First"),
                Waypoint(MapPoint.from_percent(2248, 30.0, 40.0), "Second"),
            ),
            title="Test Route",
        )
        macro = waypoints.to_macro()
        assert "/way reset all" in macro
        assert macro.count("/way #") == 2
        assert "# Test Route" in macro

    def test_macro_can_skip_reset(self) -> None:
        waypoints = WaypointSet(
            waypoints=(Waypoint(MapPoint.from_percent(2248, 10.0, 20.0)),)
        )
        assert "reset" not in waypoints.to_macro(include_reset=False)

    def test_zones_are_deduplicated_in_order(self) -> None:
        waypoints = WaypointSet(
            waypoints=(
                Waypoint(MapPoint.from_percent(2248, 10.0, 20.0)),
                Waypoint(MapPoint.from_percent(2248, 30.0, 40.0)),
                Waypoint(MapPoint.from_percent(2339, 50.0, 50.0)),
            )
        )
        assert waypoints.zones == ("Isle of Dorn", "Dornogal")

    def test_empty_set_is_falsy(self) -> None:
        assert not WaypointSet(waypoints=())
