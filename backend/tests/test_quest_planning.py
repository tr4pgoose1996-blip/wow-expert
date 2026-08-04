"""Tests for quest optimisation, recommendations, search, and waypoints."""

from __future__ import annotations

import pytest

from app.navigation.router import TravelContext
from app.navigation.tomtom import parse_way_command
from app.quests.graph import CharacterProgress, QuestGraph
from app.quests.models import Quest, QuestObjective, QuestType
from app.quests.optimizer import UNREACHABLE_COST_SECONDS, optimize_quest_route
from app.quests.recommender import (
    campaign_progress,
    recommend_quests,
    search_quests,
)
from app.quests.seed_data import build_seed_graph
from app.quests.waypoints import quest_waypoints, route_waypoints
from app.world.geography import Expansion, MapPoint

TWW = Expansion.WAR_WITHIN
ISLE = 2248
DORNOGAL = 2339
DEEPS = 2214


def _p(map_id: int, x: float, y: float) -> MapPoint:
    return MapPoint.from_percent(map_id, x, y)


def q(
    quest_id: int,
    title: str,
    map_id: int,
    x: float,
    y: float,
    *,
    prerequisites: frozenset[int] = frozenset(),
    quest_type: QuestType = QuestType.SIDE,
    seconds: float = 300.0,
    level: int = 70,
    campaign: str = "",
    step: int | None = None,
) -> Quest:
    return Quest(
        quest_id=quest_id,
        title=title,
        expansion=TWW,
        quest_type=quest_type,
        map_id=map_id,
        start_point=_p(map_id, x, y),
        prerequisites=prerequisites,
        estimated_seconds=seconds,
        required_level=level,
        campaign=campaign,
        campaign_step=step,
    )


class TestRouteOptimisation:
    def test_groups_quests_by_zone(self) -> None:
        """Zone bouncing is the main thing an optimiser must prevent."""
        quests = [
            q(1, "A", ISLE, 10, 10),
            q(2, "B", DORNOGAL, 50, 50),
            q(3, "C", ISLE, 12, 12),
            q(4, "D", DORNOGAL, 52, 52),
        ]
        graph = QuestGraph(quests)
        progress = CharacterProgress(level=80, faction="alliance", map_id=DORNOGAL)
        route = optimize_quest_route(quests, graph, progress, origin_map_id=DORNOGAL)

        visited = [leg.map_id for leg in route.legs]
        assert len(visited) == len(set(visited)), "each zone visited once"

    def test_respects_prerequisite_order(self) -> None:
        quests = [
            q(1, "First", ISLE, 10, 10, step=1),
            q(2, "Second", ISLE, 90, 90, prerequisites=frozenset({1}), step=2),
        ]
        graph = QuestGraph(quests)
        route = optimize_quest_route(
            quests, graph, CharacterProgress(level=80), origin_map_id=ISLE
        )
        order = [x.quest_id for x in route.ordered_quests]
        assert order.index(1) < order.index(2)

    def test_skips_quests_with_unmet_prerequisites(self) -> None:
        graph = QuestGraph(
            [q(1, "Locked", ISLE, 10, 10, prerequisites=frozenset({99})),
             q(99, "Gate", ISLE, 20, 20)]
        )
        route = optimize_quest_route(
            [graph.require(1)], graph, CharacterProgress(level=80)
        )
        assert route.ordered_quests == ()
        assert route.skipped
        assert "Requires" in route.skipped[0][1]

    def test_prerequisite_in_the_same_route_is_not_skipped(self) -> None:
        quests = [
            q(1, "Gate", ISLE, 10, 10),
            q(2, "Locked", ISLE, 20, 20, prerequisites=frozenset({1})),
        ]
        graph = QuestGraph(quests)
        route = optimize_quest_route(
            quests, graph, CharacterProgress(level=80), origin_map_id=ISLE
        )
        assert len(route.ordered_quests) == 2
        assert not route.skipped

    def test_unreachable_zone_is_not_priced_at_zero(self) -> None:
        """Charging 0 would make the least-understood zones look free."""
        quests = [
            q(1, "Here", DORNOGAL, 50, 50),
            q(2, "Nowhere", 999_998, 50, 50),
        ]
        graph = QuestGraph(quests, validate=False)
        route = optimize_quest_route(
            quests, graph, CharacterProgress(level=80), origin_map_id=DORNOGAL
        )
        estimated = [leg for leg in route.legs if leg.travel_estimated]
        if estimated:
            assert all(
                leg.travel_to_seconds == UNREACHABLE_COST_SECONDS
                for leg in estimated
            )

    def test_two_opt_does_not_lose_waypoints(self) -> None:
        quests = [
            q(i, f"Q{i}", ISLE, 10.0 + i * 7, 10.0 + (i * 13) % 70)
            for i in range(1, 9)
        ]
        graph = QuestGraph(quests)
        route = optimize_quest_route(
            quests, graph, CharacterProgress(level=80), origin_map_id=ISLE
        )
        total_points = sum(len(leg.waypoints) for leg in route.legs)
        assert total_points == 8

    def test_empty_input_is_handled(self) -> None:
        graph = QuestGraph([])
        route = optimize_quest_route([], graph, CharacterProgress())
        assert route.legs == ()
        assert route.total_seconds == 0.0

    def test_totals_add_up(self) -> None:
        quests = [q(1, "A", ISLE, 10, 10, seconds=300)]
        graph = QuestGraph(quests)
        route = optimize_quest_route(
            quests, graph, CharacterProgress(level=80), origin_map_id=ISLE
        )
        assert route.total_seconds == pytest.approx(
            sum(leg.total_seconds for leg in route.legs)
        )

    def test_serialises(self) -> None:
        quests = [q(1, "A", ISLE, 10, 10)]
        graph = QuestGraph(quests)
        payload = optimize_quest_route(
            quests, graph, CharacterProgress(level=80), origin_map_id=ISLE
        ).as_dict()
        assert payload["quest_count"] == 1
        assert "travel_estimated" in payload["legs"][0]


class TestRecommendations:
    @pytest.fixture
    def graph(self) -> QuestGraph:
        return QuestGraph(
            [
                q(1, "Campaign Here", DORNOGAL, 50, 50,
                  quest_type=QuestType.CAMPAIGN, campaign="c", step=1),
                q(2, "Side Here", DORNOGAL, 55, 55),
                q(3, "Side Far", DEEPS, 50, 50),
                q(4, "Unlock Here", DORNOGAL, 52, 52,
                  quest_type=QuestType.UNLOCK),
            ]
        )

    def test_returns_scored_results(self, graph) -> None:
        progress = CharacterProgress(level=75, faction="alliance", map_id=DORNOGAL)
        results = recommend_quests(graph, progress, limit=5)
        assert results
        assert all(0.0 <= r.score <= 1.0 for r in results)

    def test_sorted_by_score(self, graph) -> None:
        progress = CharacterProgress(level=75, faction="alliance", map_id=DORNOGAL)
        scores = [r.score for r in recommend_quests(graph, progress, limit=5)]
        assert scores == sorted(scores, reverse=True)

    def test_every_recommendation_is_justified(self, graph) -> None:
        """An unexplained recommendation cannot be judged by the player."""
        progress = CharacterProgress(level=75, faction="alliance", map_id=DORNOGAL)
        for result in recommend_quests(graph, progress, limit=5):
            assert result.reasons

    def test_unlocks_outrank_plain_side_quests(self, graph) -> None:
        progress = CharacterProgress(level=75, faction="alliance", map_id=DORNOGAL)
        results = {r.quest.quest_id: r.score for r in recommend_quests(graph, progress)}
        assert results[4] > results[2]

    def test_nearby_beats_distant_all_else_equal(self, graph) -> None:
        progress = CharacterProgress(level=75, faction="alliance", map_id=DORNOGAL)
        results = {r.quest.quest_id: r.score for r in recommend_quests(graph, progress)}
        assert results[2] > results[3]

    def test_completed_quests_are_not_recommended(self, graph) -> None:
        progress = CharacterProgress(
            completed=frozenset({1}), level=75, faction="alliance",
            map_id=DORNOGAL,
        )
        assert 1 not in {r.quest.quest_id for r in recommend_quests(graph, progress)}

    def test_limit_is_respected(self, graph) -> None:
        progress = CharacterProgress(level=75, faction="alliance", map_id=DORNOGAL)
        assert len(recommend_quests(graph, progress, limit=2)) == 2

    def test_invalid_limit_is_rejected(self, graph) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            recommend_quests(graph, CharacterProgress(), limit=0)

    def test_expansion_filter(self, graph) -> None:
        progress = CharacterProgress(level=75, faction="alliance")
        assert recommend_quests(graph, progress, expansion=Expansion.CLASSIC) == []

    def test_serialises(self, graph) -> None:
        progress = CharacterProgress(level=75, faction="alliance", map_id=DORNOGAL)
        payload = recommend_quests(graph, progress, limit=1)[0].as_dict()
        assert {"quest_id", "score", "reasons", "zone"} <= set(payload)


class TestCampaignProgress:
    @pytest.fixture
    def graph(self) -> QuestGraph:
        return QuestGraph(
            [
                q(i, f"Step {i}", ISLE, 10 + i, 10 + i,
                  quest_type=QuestType.CAMPAIGN, campaign="story", step=i,
                  prerequisites=frozenset({i - 1}) if i > 1 else frozenset())
                for i in range(1, 5)
            ]
        )

    def test_zero_at_the_start(self, graph) -> None:
        result = campaign_progress(graph, "story", CharacterProgress(level=80))
        assert result.percent == 0.0
        assert result.total == 4

    def test_partial_progress(self, graph) -> None:
        progress = CharacterProgress(completed=frozenset({1, 2}), level=80)
        assert campaign_progress(graph, "story", progress).percent == 50.0

    def test_completion_is_detected(self, graph) -> None:
        progress = CharacterProgress(completed=frozenset({1, 2, 3, 4}), level=80)
        assert campaign_progress(graph, "story", progress).is_complete

    def test_next_quests_are_actionable(self, graph) -> None:
        progress = CharacterProgress(completed=frozenset({1}), level=80)
        result = campaign_progress(graph, "story", progress)
        assert [x.quest_id for x in result.next_quests] == [2]

    def test_unknown_campaign_is_empty_not_an_error(self, graph) -> None:
        result = campaign_progress(graph, "nope", CharacterProgress())
        assert result.total == 0
        assert result.percent == 0.0


class TestSearch:
    @pytest.fixture(scope="class")
    def graph(self) -> QuestGraph:
        return build_seed_graph()

    def test_finds_by_title(self, graph) -> None:
        assert any(
            "Nerubian" in x.title for x in search_quests(graph, "nerubian")
        )

    def test_exact_title_ranks_first(self, graph) -> None:
        results = search_quests(graph, "Rock Bottom")
        assert results and results[0].title == "Rock Bottom"

    def test_is_case_insensitive(self, graph) -> None:
        assert search_quests(graph, "ROCK BOTTOM")

    def test_finds_by_npc(self, graph) -> None:
        assert any(
            x.start_npc == "Anduin Wrynn" for x in search_quests(graph, "Anduin")
        )

    def test_empty_query_returns_nothing(self, graph) -> None:
        assert search_quests(graph, "   ") == []

    def test_no_match_returns_empty(self, graph) -> None:
        assert search_quests(graph, "zzzzqqqq") == []

    def test_expansion_filter(self, graph) -> None:
        for quest in search_quests(graph, "the", expansion=Expansion.DRAGONFLIGHT):
            assert quest.expansion is Expansion.DRAGONFLIGHT

    def test_limit_is_respected(self, graph) -> None:
        assert len(search_quests(graph, "the", limit=2)) <= 2


class TestWaypointGeneration:
    def test_single_quest_waypoints(self) -> None:
        quest = Quest(
            quest_id=1,
            title="Test Quest",
            expansion=TWW,
            map_id=ISLE,
            start_point=_p(ISLE, 40.0, 50.0),
            end_point=_p(ISLE, 45.0, 55.0),
            start_npc="Giver",
            end_npc="Taker",
            objectives=(QuestObjective("Kill things", _p(ISLE, 42.0, 52.0)),),
        )
        waypoints = quest_waypoints(quest)
        assert len(waypoints) == 3
        assert "Giver" in waypoints.waypoints[0].description
        assert "Taker" in waypoints.waypoints[-1].description

    def test_route_waypoints_are_numbered(self) -> None:
        """Unnumbered points give no clue about order in game."""
        quests = [
            q(1, "A", ISLE, 10, 10),
            q(2, "B", ISLE, 20, 20),
        ]
        graph = QuestGraph(quests)
        route = optimize_quest_route(
            quests, graph, CharacterProgress(level=80), origin_map_id=ISLE
        )
        waypoints = route_waypoints(route)
        assert waypoints.waypoints[0].description.startswith("1.")
        assert waypoints.waypoints[1].description.startswith("2.")

    def test_generated_macro_is_parseable(self) -> None:
        """The output must actually work when pasted into TomTom."""
        quests = [q(1, "A", ISLE, 33.3, 66.7)]
        graph = QuestGraph(quests)
        route = optimize_quest_route(
            quests, graph, CharacterProgress(level=80), origin_map_id=ISLE
        )
        macro = route_waypoints(route).to_macro()
        way_lines = [ln for ln in macro.splitlines() if ln.startswith("/way #")]
        assert way_lines
        for line in way_lines:
            assert parse_way_command(line) is not None

    def test_macro_starts_with_a_reset(self) -> None:
        quests = [q(1, "A", ISLE, 10, 10)]
        graph = QuestGraph(quests)
        route = optimize_quest_route(
            quests, graph, CharacterProgress(level=80), origin_map_id=ISLE
        )
        assert "/way reset all" in route_waypoints(route).to_macro()


class TestEndToEnd:
    """The full stack over the seed data, as the API exercises it."""

    @pytest.fixture(scope="class")
    def graph(self) -> QuestGraph:
        return build_seed_graph()

    def test_new_character_gets_a_startable_recommendation(self, graph) -> None:
        progress = CharacterProgress(level=70, faction="alliance", map_id=DORNOGAL)
        results = recommend_quests(graph, progress, limit=5)
        assert results
        top = results[0].quest
        assert graph.missing_prerequisites(top.quest_id, progress) == []

    def test_mid_campaign_character_gets_the_next_step(self, graph) -> None:
        progress = CharacterProgress(
            completed=frozenset({78706, 78707}),
            level=78, faction="alliance", map_id=DORNOGAL,
        )
        if results := recommend_quests(graph, progress, limit=10):
            ids = {r.quest.quest_id for r in results}
        else:
            ids = set()
        assert 78708 in ids, "the next campaign step should be recommended"

    def test_full_plan_produces_a_pasteable_macro(self, graph) -> None:
        progress = CharacterProgress(
            completed=frozenset({78706, 78707}),
            level=78, faction="alliance", map_id=DORNOGAL,
        )
        available = graph.available_quests(progress)
        route = optimize_quest_route(
            available, graph, progress,
            context=TravelContext(faction="alliance", level=78),
            origin_map_id=DORNOGAL,
        )
        macro = route_waypoints(route).to_macro()
        assert macro.count("/way #") >= 1
        for line in macro.splitlines():
            if line.startswith("/way #"):
                assert parse_way_command(line) is not None
