"""Tests for the Quest Intelligence System."""

from __future__ import annotations

import pytest

from app.quests.graph import CharacterProgress, GraphIntegrityError, QuestGraph
from app.quests.models import Quest, QuestChain, QuestObjective, QuestStatus, QuestType
from app.quests.seed_data import build_seed_graph
from app.world.geography import Expansion, MapPoint

TWW = Expansion.WAR_WITHIN
ISLE = 2248


def q(
    quest_id: int,
    title: str = "Quest",
    *,
    prerequisites: frozenset[int] = frozenset(),
    quest_type: QuestType = QuestType.CAMPAIGN,
    campaign: str = "test",
    step: int | None = None,
    level: int = 1,
    faction: str | None = None,
    map_id: int | None = ISLE,
    leads_to: str = "",
) -> Quest:
    return Quest(
        quest_id=quest_id,
        title=title,
        expansion=TWW,
        quest_type=quest_type,
        prerequisites=prerequisites,
        campaign=campaign,
        campaign_step=step,
        required_level=level,
        faction=faction,
        map_id=map_id,
        leads_to_campaign=leads_to,
    )


class TestQuestModel:
    def test_rejects_self_dependency(self) -> None:
        with pytest.raises(ValueError, match="itself"):
            q(1, prerequisites=frozenset({1}))

    def test_rejects_blank_title(self) -> None:
        with pytest.raises(ValueError, match="no title"):
            Quest(quest_id=1, title="  ", expansion=TWW)

    def test_rejects_bad_id(self) -> None:
        with pytest.raises(ValueError, match="Invalid quest id"):
            Quest(quest_id=0, title="X", expansion=TWW)

    def test_rejects_bad_faction(self) -> None:
        with pytest.raises(ValueError, match="invalid faction"):
            Quest(quest_id=1, title="X", expansion=TWW, faction="pandaren")

    def test_eligibility_respects_faction(self) -> None:
        quest = q(1, faction="alliance")
        assert quest.is_eligible_for(faction="alliance")
        assert not quest.is_eligible_for(faction="horde")

    def test_eligibility_respects_class(self) -> None:
        quest = Quest(
            quest_id=1, title="Mage Only", expansion=TWW,
            classes=frozenset({"mage"}),
        )
        assert quest.is_eligible_for(character_class="mage")
        assert not quest.is_eligible_for(character_class="warrior")

    def test_turn_in_falls_back_to_start(self) -> None:
        point = MapPoint.from_percent(ISLE, 50.0, 50.0)
        quest = Quest(
            quest_id=1, title="X", expansion=TWW, start_point=point
        )
        assert quest.turn_in_point == point

    def test_objective_needs_text(self) -> None:
        with pytest.raises(ValueError, match="description"):
            QuestObjective("   ")

    def test_unlock_outranks_campaign(self) -> None:
        """Unlocks gate other content, so they come first."""
        assert QuestType.UNLOCK.priority < QuestType.CAMPAIGN.priority

    def test_repeatables_are_flagged(self) -> None:
        assert QuestType.DAILY.is_repeatable
        assert not QuestType.CAMPAIGN.is_repeatable


class TestGraphIntegrity:
    def test_duplicate_ids_are_refused(self) -> None:
        with pytest.raises(GraphIntegrityError, match="Duplicate"):
            QuestGraph([q(1, "A"), q(1, "B")])

    def test_cycles_are_refused(self) -> None:
        """A cycle makes the whole chain permanently unavailable."""
        with pytest.raises(GraphIntegrityError, match="cycle"):
            QuestGraph(
                [
                    q(1, prerequisites=frozenset({3})),
                    q(2, prerequisites=frozenset({1})),
                    q(3, prerequisites=frozenset({2})),
                ]
            )

    def test_dangling_prerequisite_is_reported(self) -> None:
        problems = QuestGraph(
            [q(1, prerequisites=frozenset({99}))], validate=False
        ).validate()
        assert any("unknown quest 99" in p for p in problems)

    def test_cross_faction_dependency_is_reported(self) -> None:
        """That chain would be dead for everyone."""
        problems = QuestGraph(
            [
                q(1, faction="horde"),
                q(2, faction="alliance", prerequisites=frozenset({1})),
            ],
            validate=False,
        ).validate()
        assert any("horde-only" in p for p in problems)

    def test_chain_referencing_unknown_quest_is_reported(self) -> None:
        chain = QuestChain(
            key="c", name="C", expansion=TWW, quest_ids=(1, 999)
        )
        problems = QuestGraph([q(1)], [chain], validate=False).validate()
        assert any("unknown quest 999" in p for p in problems)

    def test_deep_chain_does_not_hit_recursion_limits(self) -> None:
        """Iterative DFS: an expansion chain can be very long."""
        quests = [q(1)]
        quests += [
            q(i, prerequisites=frozenset({i - 1})) for i in range(2, 1201)
        ]
        graph = QuestGraph(quests)
        assert len(graph) == 1200


class TestStatusEvaluation:
    @pytest.fixture
    def graph(self) -> QuestGraph:
        return QuestGraph(
            [
                q(1, "First", step=1),
                q(2, "Second", prerequisites=frozenset({1}), step=2),
                q(3, "Third", prerequisites=frozenset({2}), step=3),
                q(10, "Gated", level=80),
                q(20, "Horde Only", faction="horde"),
            ]
        )

    def test_first_quest_is_available(self, graph) -> None:
        progress = CharacterProgress(level=80, faction="alliance")
        assert graph.status_for(1, progress) is QuestStatus.AVAILABLE

    def test_dependent_is_locked(self, graph) -> None:
        progress = CharacterProgress(level=80, faction="alliance")
        assert graph.status_for(2, progress) is QuestStatus.LOCKED

    def test_completion_unlocks_the_next(self, graph) -> None:
        progress = CharacterProgress(
            completed=frozenset({1}), level=80, faction="alliance"
        )
        assert graph.status_for(2, progress) is QuestStatus.AVAILABLE

    def test_completed_is_reported(self, graph) -> None:
        progress = CharacterProgress(completed=frozenset({1}), level=80)
        assert graph.status_for(1, progress) is QuestStatus.COMPLETED

    def test_in_progress_is_reported(self, graph) -> None:
        progress = CharacterProgress(in_progress=frozenset({1}), level=80)
        assert graph.status_for(1, progress) is QuestStatus.IN_PROGRESS

    def test_level_gate_locks(self, graph) -> None:
        progress = CharacterProgress(level=70, faction="alliance")
        assert graph.status_for(10, progress) is QuestStatus.LOCKED

    def test_wrong_faction_is_ineligible_not_locked(self, graph) -> None:
        """Ineligible is permanent; locked merely awaits progress."""
        progress = CharacterProgress(level=80, faction="alliance")
        assert graph.status_for(20, progress) is QuestStatus.INELIGIBLE

    def test_unknown_quest_raises(self, graph) -> None:
        with pytest.raises(KeyError):
            graph.status_for(999, CharacterProgress())


class TestPrerequisites:
    @pytest.fixture
    def graph(self) -> QuestGraph:
        return QuestGraph(
            [
                q(1, "A", step=1),
                q(2, "B", prerequisites=frozenset({1}), step=2),
                q(3, "C", prerequisites=frozenset({2}), step=3),
                q(4, "D", prerequisites=frozenset({3}), step=4),
            ]
        )

    def test_transitive_prerequisites_are_returned(self, graph) -> None:
        """A half-answer naming only the direct parent is not useful."""
        missing = graph.missing_prerequisites(4, CharacterProgress(level=80))
        assert [m.title for m in missing] == ["A", "B", "C"]

    def test_completed_prerequisites_are_omitted(self, graph) -> None:
        progress = CharacterProgress(completed=frozenset({1, 2}), level=80)
        assert [m.title for m in graph.missing_prerequisites(4, progress)] == ["C"]

    def test_path_includes_the_target(self, graph) -> None:
        path = graph.path_to(4, CharacterProgress(level=80))
        assert [p.title for p in path] == ["A", "B", "C", "D"]

    def test_path_to_completed_quest_is_empty(self, graph) -> None:
        progress = CharacterProgress(completed=frozenset({4}), level=80)
        assert graph.path_to(4, progress) == []

    def test_blocked_by_lists_only_direct_parents(self, graph) -> None:
        blocked = graph.blocked_by(4, CharacterProgress(level=80))
        assert [b.title for b in blocked] == ["C"]

    def test_no_prerequisites_returns_empty(self, graph) -> None:
        assert graph.missing_prerequisites(1, CharacterProgress()) == []


class TestTopologicalOrder:
    def test_prerequisites_always_precede_dependants(self) -> None:
        graph = QuestGraph(
            [
                q(1, "A", step=1),
                q(2, "B", prerequisites=frozenset({1}), step=2),
                q(3, "C", prerequisites=frozenset({1}), step=3),
                q(4, "D", prerequisites=frozenset({2, 3}), step=4),
            ]
        )
        ordered = graph.topological_order(graph.quests)
        positions = {quest.quest_id: i for i, quest in enumerate(ordered)}
        assert positions[1] < positions[2] < positions[4]
        assert positions[1] < positions[3] < positions[4]

    def test_is_stable_across_runs(self) -> None:
        graph = QuestGraph([q(i, f"Q{i}", step=i) for i in range(1, 8)])
        first = [x.quest_id for x in graph.topological_order(graph.quests)]
        second = [x.quest_id for x in graph.topological_order(graph.quests)]
        assert first == second

    def test_no_quests_are_dropped(self) -> None:
        graph = QuestGraph(
            [q(1), q(2, prerequisites=frozenset({1})), q(3), q(4)]
        )
        assert len(graph.topological_order(graph.quests)) == 4


class TestBreadcrumbs:
    @pytest.fixture
    def graph(self) -> QuestGraph:
        return QuestGraph(
            [
                q(1, "Go See Someone", quest_type=QuestType.BREADCRUMB,
                  campaign="", leads_to="target_campaign"),
                q(2, "Target Start", campaign="target_campaign", step=1),
            ]
        )

    def test_breadcrumb_is_available_before_the_target(self, graph) -> None:
        assert graph.status_for(1, CharacterProgress(level=80)) is (
            QuestStatus.AVAILABLE
        )

    def test_breadcrumb_becomes_obsolete_once_target_started(self, graph) -> None:
        """Recommending these is how quest helpers waste a player's time."""
        progress = CharacterProgress(in_progress=frozenset({2}), level=80)
        assert graph.status_for(1, progress) is QuestStatus.OBSOLETE

    def test_breadcrumb_obsolete_once_target_completed(self, graph) -> None:
        progress = CharacterProgress(completed=frozenset({2}), level=80)
        assert graph.status_for(1, progress) is QuestStatus.OBSOLETE

    def test_obsolete_list_reports_them(self, graph) -> None:
        progress = CharacterProgress(completed=frozenset({2}), level=80)
        assert [x.quest_id for x in graph.obsolete_breadcrumbs(progress)] == [1]

    def test_available_quests_excludes_obsolete(self, graph) -> None:
        progress = CharacterProgress(completed=frozenset({2}), level=80)
        assert 1 not in {x.quest_id for x in graph.available_quests(progress)}


class TestSeedGraph:
    @pytest.fixture(scope="class")
    def graph(self) -> QuestGraph:
        return build_seed_graph()

    def test_seed_data_is_valid(self, graph) -> None:
        assert graph.validate() == []

    def test_covers_multiple_expansions(self, graph) -> None:
        expansions = {quest.expansion for quest in graph.quests}
        assert len(expansions) >= 4

    def test_campaign_spine_is_linear(self, graph) -> None:
        quests = graph.campaign_quests("tww_campaign")
        steps = [q.campaign_step for q in quests if q.campaign_step]
        assert steps == sorted(steps)

    def test_full_campaign_path_resolves(self, graph) -> None:
        path = graph.path_to(78712, CharacterProgress(level=80, faction="alliance"))
        first_id = path[0].quest_id if path else None
        assert first_id == 78706
        assert path[-1].quest_id == 78712
