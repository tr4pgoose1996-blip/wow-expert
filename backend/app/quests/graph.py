"""The quest dependency graph.

A directed acyclic graph where an edge A → B means "A must be completed
before B becomes available". Every other quest feature is a query over this
structure:

===========================  ====================================
Feature                      Graph operation
===========================  ====================================
Missing prerequisites        Reverse reachability from a target
Available quests             Nodes whose in-edges are satisfied
Story progression            Completed fraction of a campaign
Breadcrumb detection         Breadcrumbs whose target is reached
Optimal order                Topological sort within a campaign
===========================  ====================================

The graph validates itself on build: a cycle would make an entire chain
permanently unavailable and hang any naive traversal, so it is caught at
construction rather than at query time.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from app.core.logging import get_logger
from app.quests.models import Quest, QuestChain, QuestStatus
from app.world.geography import Expansion

logger = get_logger(__name__)

__all__ = [
    "CharacterProgress",
    "GraphIntegrityError",
    "QuestGraph",
]


class GraphIntegrityError(ValueError):
    """Raised when the quest graph is structurally invalid."""


@dataclass(frozen=True, slots=True)
class CharacterProgress:
    """A character's questing state, used to evaluate the graph.

    Deliberately a value object rather than a database row: the graph must
    be queryable for hypothetical characters ("what would a level 70 mage
    see?") as well as real ones.
    """

    completed: frozenset[int] = frozenset()
    in_progress: frozenset[int] = frozenset()
    level: int = 1
    faction: str | None = None
    character_class: str | None = None
    race: str | None = None
    map_id: int | None = None

    def has_completed(self, quest_id: int) -> bool:
        return quest_id in self.completed

    def status_of(self, quest_id: int) -> QuestStatus | None:
        """Directly-known status, before graph evaluation."""
        if quest_id in self.completed:
            return QuestStatus.COMPLETED
        if quest_id in self.in_progress:
            return QuestStatus.IN_PROGRESS
        return None


class QuestGraph:
    """An immutable, validated quest dependency graph."""

    def __init__(
        self,
        quests: list[Quest],
        chains: list[QuestChain] | None = None,
        *,
        validate: bool = True,
    ) -> None:
        self._quests: dict[int, Quest] = {}
        for quest in quests:
            if quest.quest_id in self._quests:
                raise GraphIntegrityError(
                    f"Duplicate quest id {quest.quest_id}: "
                    f"{self._quests[quest.quest_id].title!r} and {quest.title!r}"
                )
            self._quests[quest.quest_id] = quest

        self._chains: dict[str, QuestChain] = {c.key: c for c in (chains or [])}

        # Forward edges are derived rather than trusted: `unlocks` on the
        # quest is a convenience field that can drift out of sync with
        # `prerequisites`, and prerequisites are the authoritative direction.
        self._dependents: dict[int, set[int]] = {qid: set() for qid in self._quests}
        for quest in self._quests.values():
            for prerequisite in quest.prerequisites:
                if prerequisite in self._dependents:
                    self._dependents[prerequisite].add(quest.quest_id)

        self._by_campaign: dict[str, list[Quest]] = {}
        for quest in self._quests.values():
            if quest.campaign:
                self._by_campaign.setdefault(quest.campaign, []).append(quest)
        for campaign_quests in self._by_campaign.values():
            campaign_quests.sort(
                key=lambda q: (
                    q.campaign_step is None,
                    q.campaign_step or 0,
                    q.quest_id,
                )
            )

        if validate:
            problems = self.validate()
            if problems:
                raise GraphIntegrityError(
                    f"Quest graph is invalid ({len(problems)} problem(s)): "
                    + "; ".join(problems[:5])
                )

    # ------------------------------------------------------------- accessors

    def __len__(self) -> int:
        return len(self._quests)

    def __contains__(self, quest_id: object) -> bool:
        return quest_id in self._quests

    @property
    def quests(self) -> list[Quest]:
        return list(self._quests.values())

    @property
    def campaigns(self) -> list[str]:
        return sorted(self._by_campaign)

    def get(self, quest_id: int) -> Quest | None:
        return self._quests.get(quest_id)

    def require(self, quest_id: int) -> Quest:
        quest = self._quests.get(quest_id)
        if quest is None:
            raise KeyError(f"Unknown quest id {quest_id}")
        return quest

    def get_chain(self, key: str) -> QuestChain | None:
        return self._chains.get(key)

    def campaign_quests(self, campaign: str) -> list[Quest]:
        """Quests in a campaign, in intended play order."""
        return list(self._by_campaign.get(campaign, []))

    def dependents_of(self, quest_id: int) -> list[Quest]:
        """Quests directly unlocked by this one."""
        return [self._quests[q] for q in sorted(self._dependents.get(quest_id, ()))]

    # ------------------------------------------------------------ validation

    def validate(self) -> list[str]:
        """Return structural problems; empty means healthy."""
        problems: list[str] = []

        for quest in self._quests.values():
            for prerequisite in sorted(quest.prerequisites):
                if prerequisite not in self._quests:
                    problems.append(
                        f"Quest {quest.quest_id} ({quest.title!r}) requires "
                        f"unknown quest {prerequisite}"
                    )
                    continue
                parent = self._quests[prerequisite]
                # A quest cannot depend on one restricted to the other
                # faction: that chain would be permanently dead.
                if (
                    parent.faction is not None
                    and quest.faction is not None
                    and parent.faction != quest.faction
                ):
                    problems.append(
                        f"Quest {quest.quest_id} ({quest.faction}) requires "
                        f"{prerequisite} which is {parent.faction}-only"
                    )

        problems.extend(self._detect_cycles())

        for chain in self._chains.values():
            for quest_id in chain.quest_ids:
                if quest_id not in self._quests:
                    problems.append(
                        f"Chain {chain.key!r} references unknown quest {quest_id}"
                    )

        return problems

    def _detect_cycles(self) -> list[str]:
        """Find dependency cycles via iterative depth-first search.

        Iterative rather than recursive: a long expansion chain can exceed
        Python's recursion limit, and crashing on valid data would be worse
        than the slightly longer code.
        """
        WHITE, GREY, BLACK = 0, 1, 2  # noqa: N806 (graph colour states)
        colour: dict[int, int] = dict.fromkeys(self._quests, WHITE)
        problems: list[str] = []

        for root in self._quests:
            if colour[root] != WHITE:
                continue
            stack: list[tuple[int, list[int]]] = [(root, [])]
            while stack:
                node, path = stack.pop()
                if node == -1:
                    # Sentinel: children of `path[-1]` are done.
                    colour[path[-1]] = BLACK
                    continue
                if colour[node] == GREY:
                    cycle = [*path[path.index(node):], node] if node in path else [node]
                    problems.append(
                        "Dependency cycle: "
                        + " -> ".join(str(item) for item in cycle)
                    )
                    continue
                if colour[node] == BLACK:
                    continue
                colour[node] = GREY
                new_path = [*path, node]
                stack.append((-1, new_path))
                quest = self._quests[node]
                for prerequisite in sorted(quest.prerequisites):
                    if prerequisite in self._quests and colour[prerequisite] != BLACK:
                        stack.append((prerequisite, new_path))

        return problems

    # ------------------------------------------------------- status queries

    def status_for(self, quest_id: int, progress: CharacterProgress) -> QuestStatus:
        """Evaluate one quest's status for a character."""
        quest = self.require(quest_id)

        known = progress.status_of(quest_id)
        if known is not None:
            return known

        if not quest.is_eligible_for(
            faction=progress.faction,
            character_class=progress.character_class,
            race=progress.race,
            level=None,  # level gates availability, not eligibility
        ):
            return QuestStatus.INELIGIBLE

        missing = quest.prerequisites - progress.completed
        if missing:
            chain = self._chain_containing(quest_id)
            if chain is None or not all(
                chain.satisfies_any_of(m, set(progress.completed)) for m in missing
            ):
                return QuestStatus.LOCKED

        if progress.level < quest.required_level:
            return QuestStatus.LOCKED

        if quest.is_breadcrumb and self._breadcrumb_is_obsolete(quest, progress):
            return QuestStatus.OBSOLETE

        return QuestStatus.AVAILABLE

    def _chain_containing(self, quest_id: int) -> QuestChain | None:
        for chain in self._chains.values():
            if quest_id in chain.quest_ids:
                return chain
        return None

    def _breadcrumb_is_obsolete(
        self, quest: Quest, progress: CharacterProgress
    ) -> bool:
        """Whether a breadcrumb has been overtaken by actual progress.

        Breadcrumbs point players at content they have not found yet. Once
        that content is underway the breadcrumb is noise, and recommending
        it wastes the player's time — the single most common complaint about
        naive quest helpers.
        """
        target = quest.leads_to_campaign
        if not target:
            return False
        for candidate in self._by_campaign.get(target, []):
            if candidate.quest_id in progress.completed:
                return True
            if candidate.quest_id in progress.in_progress:
                return True
        return False

    def available_quests(
        self, progress: CharacterProgress, *, include_in_progress: bool = True
    ) -> list[Quest]:
        """Every quest the character can pick up right now."""
        results: list[Quest] = []
        for quest in self._quests.values():
            if quest.quest_type.is_repeatable:
                continue
            status = self.status_for(quest.quest_id, progress)
            if status is QuestStatus.AVAILABLE or (
                include_in_progress and status is QuestStatus.IN_PROGRESS
            ):
                results.append(quest)
        results.sort(
            key=lambda q: (q.quest_type.priority, q.required_level, q.quest_id)
        )
        return results

    def missing_prerequisites(
        self, quest_id: int, progress: CharacterProgress
    ) -> list[Quest]:
        """Every uncompleted quest required before a target, in play order.

        Walks the full transitive closure, not just direct parents: telling a
        player they need quest X when X itself needs three others is a
        half-answer.
        """
        target = self.require(quest_id)
        missing: dict[int, Quest] = {}
        queue: deque[int] = deque(target.prerequisites)
        seen: set[int] = set()

        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            if current in progress.completed:
                continue
            quest = self._quests.get(current)
            if quest is None:
                continue
            missing[current] = quest
            queue.extend(quest.prerequisites)

        return self.topological_order(list(missing.values()))

    def topological_order(self, quests: list[Quest]) -> list[Quest]:
        """Order quests so prerequisites always precede dependants.

        Kahn's algorithm restricted to the given subset. Ties break on
        campaign step then id, so the order is stable and matches the
        intended narrative sequence rather than dictionary ordering.
        """
        subset = {q.quest_id: q for q in quests}
        indegree = {
            qid: len(q.prerequisites & subset.keys()) for qid, q in subset.items()
        }

        ready = sorted(
            (qid for qid, degree in indegree.items() if degree == 0),
            key=lambda qid: self._sort_key(subset[qid]),
        )
        ordered: list[Quest] = []

        while ready:
            current = ready.pop(0)
            ordered.append(subset[current])
            newly_ready: list[int] = []
            for dependent in self._dependents.get(current, ()):
                if dependent not in indegree:
                    continue
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    newly_ready.append(dependent)
            if newly_ready:
                ready.extend(newly_ready)
                ready.sort(key=lambda qid: self._sort_key(subset[qid]))

        if len(ordered) != len(subset):
            # Validation rejects cycles at construction, so reaching here
            # means the subset was hand-built; degrade to a stable order
            # rather than silently dropping quests.
            remaining = [q for qid, q in subset.items() if qid not in {
                o.quest_id for o in ordered
            }]
            logger.warning(
                "Topological sort incomplete; %d quest(s) in a cycle",
                len(remaining),
            )
            ordered.extend(sorted(remaining, key=self._sort_key))

        return ordered

    @staticmethod
    def _sort_key(quest: Quest) -> tuple:
        return (
            quest.campaign_step is None,
            quest.campaign_step or 0,
            quest.quest_type.priority,
            quest.quest_id,
        )

    # -------------------------------------------------------- path to target

    def path_to(self, quest_id: int, progress: CharacterProgress) -> list[Quest]:
        """The full ordered sequence to reach and complete a target quest."""
        target = self.require(quest_id)
        if target.quest_id in progress.completed:
            return []
        return [*self.missing_prerequisites(quest_id, progress), target]

    def blocked_by(self, quest_id: int, progress: CharacterProgress) -> list[Quest]:
        """Only the *immediate* unmet prerequisites."""
        quest = self.require(quest_id)
        return [
            self._quests[p]
            for p in sorted(quest.prerequisites - progress.completed)
            if p in self._quests
        ]

    # ----------------------------------------------------------- expansions

    def quests_for_expansion(self, expansion: Expansion) -> list[Quest]:
        return [q for q in self._quests.values() if q.expansion is expansion]

    def breadcrumbs(self) -> list[Quest]:
        return [q for q in self._quests.values() if q.is_breadcrumb]

    def obsolete_breadcrumbs(self, progress: CharacterProgress) -> list[Quest]:
        """Breadcrumbs the player should skip."""
        return [
            quest
            for quest in self.breadcrumbs()
            if quest.quest_id not in progress.completed
            and self._breadcrumb_is_obsolete(quest, progress)
        ]

    def stats(self) -> dict[str, int]:
        by_type: dict[str, int] = {}
        for quest in self._quests.values():
            by_type[quest.quest_type.value] = by_type.get(quest.quest_type.value, 0) + 1
        return {
            "quests": len(self._quests),
            "chains": len(self._chains),
            "campaigns": len(self._by_campaign),
            "edges": sum(len(d) for d in self._dependents.values()),
            **{f"type_{k}": v for k, v in sorted(by_type.items())},
        }
