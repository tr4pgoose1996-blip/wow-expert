"""Quest Intelligence: dependency graphs, routing, and recommendations.

The quest graph is the core abstraction; campaign tracking, prerequisite
resolution, breadcrumb detection, optimisation, and recommendations are all
queries over it.
"""

from app.quests.graph import CharacterProgress, GraphIntegrityError, QuestGraph
from app.quests.models import (
    Quest,
    QuestChain,
    QuestObjective,
    QuestStatus,
    QuestType,
)
from app.quests.optimizer import OptimizedRoute, RouteLeg, optimize_quest_route
from app.quests.recommender import (
    CampaignProgress,
    QuestRecommendation,
    campaign_progress,
    recommend_quests,
    search_quests,
)
from app.quests.waypoints import quest_waypoints, route_waypoints

__all__ = [
    "CampaignProgress",
    "CharacterProgress",
    "GraphIntegrityError",
    "OptimizedRoute",
    "Quest",
    "QuestChain",
    "QuestGraph",
    "QuestObjective",
    "QuestRecommendation",
    "QuestStatus",
    "QuestType",
    "RouteLeg",
    "campaign_progress",
    "optimize_quest_route",
    "quest_waypoints",
    "recommend_quests",
    "route_waypoints",
    "search_quests",
]
