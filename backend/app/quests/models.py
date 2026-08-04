"""Quest domain model.

The core abstraction is a directed acyclic graph of quests linked by
prerequisites. Everything else — campaign tracking, missing prerequisites,
story progression, breadcrumb detection, recommendations — is a query over
that graph.

Quest ids are Blizzard's own, so data from the Game Data API, a character's
completed-quest list, and this model all key on the same identifiers with no
translation layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.world.geography import Expansion, MapPoint

__all__ = [
    "Quest",
    "QuestChain",
    "QuestObjective",
    "QuestStatus",
    "QuestType",
]


class QuestType(StrEnum):
    """What kind of quest this is, which drives how it is prioritised."""

    #: Main story of an expansion or zone.
    CAMPAIGN = "campaign"
    #: Ordinary side content.
    SIDE = "side"
    #: Points at content elsewhere; obsolete once that content is reached.
    BREADCRUMB = "breadcrumb"
    #: Repeats daily.
    DAILY = "daily"
    #: Repeats weekly.
    WEEKLY = "weekly"
    #: Profession training or recipe acquisition.
    PROFESSION = "profession"
    #: Class-specific storyline.
    CLASS = "class"
    #: Unlocks a feature, flight, or zone.
    UNLOCK = "unlock"
    #: World quest — dynamic, not part of the static graph.
    WORLD = "world"

    @property
    def is_repeatable(self) -> bool:
        return self in (QuestType.DAILY, QuestType.WEEKLY, QuestType.WORLD)

    @property
    def priority(self) -> int:
        """Recommendation weight; lower sorts first.

        Unlocks outrank campaign because they gate other content — a flight
        path or a zone unlock makes everything after it cheaper.
        """
        return _TYPE_PRIORITY[self]


_TYPE_PRIORITY: dict[QuestType, int] = {
    QuestType.UNLOCK: 0,
    QuestType.CAMPAIGN: 1,
    QuestType.CLASS: 2,
    QuestType.BREADCRUMB: 3,
    QuestType.PROFESSION: 4,
    QuestType.SIDE: 5,
    QuestType.WEEKLY: 6,
    QuestType.DAILY: 7,
    QuestType.WORLD: 8,
}


class QuestStatus(StrEnum):
    """A quest's state for one character."""

    #: Prerequisites met, not yet done.
    AVAILABLE = "available"
    #: Currently in the quest log.
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    #: Prerequisites not yet met.
    LOCKED = "locked"
    #: Cannot ever be completed — wrong faction, class, or race.
    INELIGIBLE = "ineligible"
    #: Superseded: a breadcrumb whose destination content is already reached.
    OBSOLETE = "obsolete"

    @property
    def is_actionable(self) -> bool:
        return self in (QuestStatus.AVAILABLE, QuestStatus.IN_PROGRESS)


@dataclass(frozen=True, slots=True)
class QuestObjective:
    """One step within a quest, optionally with a location."""

    description: str
    location: MapPoint | None = None
    #: Blizzard's objective index, when known.
    index: int = 0

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("Quest objective needs a description")


@dataclass(frozen=True, slots=True)
class Quest:
    """A single quest and its place in the graph.

    Frozen because the quest graph is shared, cached, and read concurrently;
    a mutable node would be a data race waiting to happen. Per-character
    state lives in :class:`QuestStatus` results, never on the quest itself.
    """

    quest_id: int
    title: str
    expansion: Expansion
    quest_type: QuestType = QuestType.SIDE
    #: Zone where the quest is picked up.
    map_id: int | None = None
    #: Where to collect the quest.
    start_point: MapPoint | None = None
    #: Where to hand it in; often the same NPC.
    end_point: MapPoint | None = None
    start_npc: str = ""
    end_npc: str = ""
    #: Quests that must be completed first. All of them, unless the quest
    #: appears in an ``any_of`` group on the chain.
    prerequisites: frozenset[int] = frozenset()
    #: Quests unlocked by completing this one. Derived, but stored for fast
    #: forward traversal without inverting the whole graph.
    unlocks: frozenset[int] = frozenset()
    required_level: int = 1
    #: ``None`` means both factions.
    faction: str | None = None
    #: Empty means all classes.
    classes: frozenset[str] = frozenset()
    #: Empty means all races.
    races: frozenset[str] = frozenset()
    objectives: tuple[QuestObjective, ...] = ()
    #: Campaign or storyline this belongs to, for progression tracking.
    campaign: str = ""
    #: Ordering within the campaign; ``None`` when unordered.
    campaign_step: int | None = None
    #: For breadcrumbs: the quest or campaign this points at.
    leads_to_campaign: str = ""
    #: Estimated completion time in seconds, excluding travel.
    estimated_seconds: float = 300.0
    experience_reward: int = 0

    def __post_init__(self) -> None:
        if self.quest_id <= 0:
            raise ValueError(f"Invalid quest id: {self.quest_id}")
        if not self.title.strip():
            raise ValueError(f"Quest {self.quest_id} has no title")
        if self.quest_id in self.prerequisites:
            raise ValueError(f"Quest {self.quest_id} lists itself as a prerequisite")
        if self.faction not in (None, "alliance", "horde"):
            raise ValueError(
                f"Quest {self.quest_id}: invalid faction {self.faction!r}"
            )
        if self.estimated_seconds < 0:
            raise ValueError(f"Quest {self.quest_id}: negative duration")

    @property
    def is_breadcrumb(self) -> bool:
        return self.quest_type is QuestType.BREADCRUMB

    @property
    def turn_in_point(self) -> MapPoint | None:
        """Where the quest ends; falls back to where it starts."""
        return self.end_point or self.start_point

    def is_eligible_for(
        self,
        *,
        faction: str | None = None,
        character_class: str | None = None,
        race: str | None = None,
        level: int | None = None,
    ) -> bool:
        """Whether a character could ever complete this quest.

        Distinct from availability: an ineligible quest is permanently
        closed, whereas a locked one merely awaits prerequisites.
        """
        if self.faction is not None and faction is not None and self.faction != faction:
            return False
        if self.classes and character_class is not None \
                and character_class not in self.classes:
            return False
        if self.races and race is not None and race not in self.races:
            return False
        return not (level is not None and level < self.required_level)


@dataclass(slots=True)
class QuestChain:
    """A named sequence of related quests, such as a zone storyline."""

    key: str
    name: str
    expansion: Expansion
    quest_ids: tuple[int, ...] = ()
    #: Groups where any one quest satisfies the requirement, not all.
    any_of_groups: tuple[frozenset[int], ...] = ()
    description: str = ""
    map_id: int | None = None
    campaign: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("Quest chain needs a key")
        if len(set(self.quest_ids)) != len(self.quest_ids):
            raise ValueError(f"Chain {self.key!r} lists a quest twice")

    def __len__(self) -> int:
        return len(self.quest_ids)

    def satisfies_any_of(self, quest_id: int, completed: set[int]) -> bool:
        """Whether an ``any_of`` group covering this quest is satisfied."""
        for group in self.any_of_groups:
            if quest_id in group and completed & group:
                return True
        return False
