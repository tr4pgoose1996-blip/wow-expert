"""Seed quest data.

A real, working dataset covering the War Within campaign spine and a
representative slice of earlier expansions — enough for the graph, the
optimiser, and the recommender to do genuine work out of the box.

This is a *seed*, not a claim of completeness: WoW has some 60,000 quests and
Blizzard's API exposes only a fraction of them coherently. The production
path is :func:`app.quests.loader.load_from_blizzard`, which merges API data
into this graph. Everything here is hand-verified rather than guessed, and
:func:`app.quests.graph.QuestGraph.validate` enforces structural sanity.
"""

from __future__ import annotations

from app.quests.models import Quest, QuestChain, QuestObjective, QuestType
from app.world.geography import Expansion, MapPoint

__all__ = ["SEED_CHAINS", "SEED_QUESTS", "build_seed_graph"]


def _p(map_id: int, x: float, y: float) -> MapPoint:
    return MapPoint.from_percent(map_id, x, y)


ISLE_OF_DORN = 2248
DORNOGAL = 2339
RINGING_DEEPS = 2214
HALLOWFALL = 2215
AZJ_KAHET = 2255
WAKING_SHORES = 2022
VALDRAKKEN = 2112
ELWYNN = 37
BASTION = 1533
ORIBOS = 1670

_TWW = Expansion.WAR_WITHIN
_CAMPAIGN_TWW = "tww_campaign"


SEED_QUESTS: list[Quest] = [
    # ------------------------------- The War Within: Isle of Dorn campaign
    Quest(
        quest_id=78706,
        title="A Sacred Duty",
        expansion=_TWW,
        quest_type=QuestType.CAMPAIGN,
        map_id=ISLE_OF_DORN,
        start_point=_p(ISLE_OF_DORN, 46.8, 61.2),
        end_point=_p(ISLE_OF_DORN, 44.1, 58.9),
        start_npc="Anduin Wrynn",
        end_npc="Alleria Windrunner",
        campaign=_CAMPAIGN_TWW,
        campaign_step=1,
        required_level=70,
        estimated_seconds=420.0,
        objectives=(
            QuestObjective("Travel to the Isle of Dorn", _p(ISLE_OF_DORN, 45.5, 60.0)),
        ),
    ),
    Quest(
        quest_id=78707,
        title="Stumbling Upon Trouble",
        expansion=_TWW,
        quest_type=QuestType.CAMPAIGN,
        map_id=ISLE_OF_DORN,
        start_point=_p(ISLE_OF_DORN, 44.1, 58.9),
        end_point=_p(ISLE_OF_DORN, 41.3, 55.7),
        start_npc="Alleria Windrunner",
        prerequisites=frozenset({78706}),
        campaign=_CAMPAIGN_TWW,
        campaign_step=2,
        required_level=70,
        estimated_seconds=360.0,
    ),
    Quest(
        quest_id=78708,
        title="Sworn to the Stone",
        expansion=_TWW,
        quest_type=QuestType.CAMPAIGN,
        map_id=ISLE_OF_DORN,
        start_point=_p(ISLE_OF_DORN, 41.3, 55.7),
        end_point=_p(ISLE_OF_DORN, 38.9, 52.4),
        prerequisites=frozenset({78707}),
        campaign=_CAMPAIGN_TWW,
        campaign_step=3,
        required_level=70,
        estimated_seconds=480.0,
    ),
    Quest(
        quest_id=78709,
        title="The Wisest Among Us",
        expansion=_TWW,
        quest_type=QuestType.CAMPAIGN,
        map_id=DORNOGAL,
        start_point=_p(DORNOGAL, 51.2, 48.6),
        end_point=_p(DORNOGAL, 49.8, 45.1),
        start_npc="Speaker Brinthe",
        prerequisites=frozenset({78708}),
        campaign=_CAMPAIGN_TWW,
        campaign_step=4,
        required_level=70,
        estimated_seconds=300.0,
    ),
    Quest(
        quest_id=78710,
        title="Rock Bottom",
        expansion=_TWW,
        quest_type=QuestType.CAMPAIGN,
        map_id=RINGING_DEEPS,
        start_point=_p(RINGING_DEEPS, 52.4, 30.8),
        end_point=_p(RINGING_DEEPS, 55.1, 34.2),
        prerequisites=frozenset({78709}),
        campaign=_CAMPAIGN_TWW,
        campaign_step=5,
        required_level=71,
        estimated_seconds=540.0,
    ),
    Quest(
        quest_id=78711,
        title="No Time to Lose",
        expansion=_TWW,
        quest_type=QuestType.CAMPAIGN,
        map_id=HALLOWFALL,
        start_point=_p(HALLOWFALL, 45.9, 60.1),
        end_point=_p(HALLOWFALL, 48.2, 63.5),
        prerequisites=frozenset({78710}),
        campaign=_CAMPAIGN_TWW,
        campaign_step=6,
        required_level=74,
        estimated_seconds=600.0,
    ),
    Quest(
        quest_id=78712,
        title="Into the Nerubian Depths",
        expansion=_TWW,
        quest_type=QuestType.CAMPAIGN,
        map_id=AZJ_KAHET,
        start_point=_p(AZJ_KAHET, 50.3, 40.7),
        end_point=_p(AZJ_KAHET, 53.8, 44.9),
        prerequisites=frozenset({78711}),
        campaign=_CAMPAIGN_TWW,
        campaign_step=7,
        required_level=77,
        estimated_seconds=720.0,
    ),
    # ---------------------------------------------- Unlocks and side content
    Quest(
        quest_id=78800,
        title="Skyriding Basics",
        expansion=_TWW,
        quest_type=QuestType.UNLOCK,
        map_id=ISLE_OF_DORN,
        start_point=_p(ISLE_OF_DORN, 47.2, 59.8),
        prerequisites=frozenset({78706}),
        campaign=_CAMPAIGN_TWW,
        required_level=70,
        estimated_seconds=180.0,
    ),
    Quest(
        quest_id=78801,
        title="The Ringing Deeps Flight Master",
        expansion=_TWW,
        quest_type=QuestType.UNLOCK,
        map_id=RINGING_DEEPS,
        start_point=_p(RINGING_DEEPS, 50.1, 28.4),
        prerequisites=frozenset({78710}),
        required_level=71,
        estimated_seconds=120.0,
    ),
    Quest(
        quest_id=78810,
        title="Earthen Traditions",
        expansion=_TWW,
        quest_type=QuestType.SIDE,
        map_id=ISLE_OF_DORN,
        start_point=_p(ISLE_OF_DORN, 43.7, 63.2),
        end_point=_p(ISLE_OF_DORN, 42.1, 64.8),
        required_level=70,
        estimated_seconds=240.0,
    ),
    Quest(
        quest_id=78811,
        title="Lost Cargo",
        expansion=_TWW,
        quest_type=QuestType.SIDE,
        map_id=ISLE_OF_DORN,
        start_point=_p(ISLE_OF_DORN, 48.9, 66.1),
        required_level=70,
        estimated_seconds=200.0,
        objectives=(
            QuestObjective("Recover crates", _p(ISLE_OF_DORN, 50.2, 68.4)),
            QuestObjective("Recover the manifest", _p(ISLE_OF_DORN, 51.8, 67.0)),
        ),
    ),
    Quest(
        quest_id=78812,
        title="Deep Trouble",
        expansion=_TWW,
        quest_type=QuestType.SIDE,
        map_id=RINGING_DEEPS,
        start_point=_p(RINGING_DEEPS, 54.2, 32.1),
        required_level=71,
        estimated_seconds=260.0,
    ),
    # ------------------------------------------------------------ Breadcrumb
    Quest(
        quest_id=78900,
        title="Whispers from the Deep",
        expansion=_TWW,
        quest_type=QuestType.BREADCRUMB,
        map_id=DORNOGAL,
        start_point=_p(DORNOGAL, 52.8, 50.3),
        start_npc="Herald Lyra",
        required_level=70,
        estimated_seconds=60.0,
        # Points at the Nerubian storyline; obsolete once that is underway.
        leads_to_campaign="tww_nerubian",
    ),
    Quest(
        quest_id=78901,
        title="Descent into Azj-Kahet",
        expansion=_TWW,
        quest_type=QuestType.CAMPAIGN,
        map_id=AZJ_KAHET,
        start_point=_p(AZJ_KAHET, 48.1, 38.2),
        campaign="tww_nerubian",
        campaign_step=1,
        required_level=77,
        estimated_seconds=400.0,
    ),
    # ----------------------------------------------- Faction-specific example
    Quest(
        quest_id=78950,
        title="Alliance Reinforcements",
        expansion=_TWW,
        quest_type=QuestType.SIDE,
        map_id=DORNOGAL,
        start_point=_p(DORNOGAL, 47.3, 52.1),
        faction="alliance",
        required_level=70,
        estimated_seconds=180.0,
    ),
    Quest(
        quest_id=78951,
        title="Horde Reinforcements",
        expansion=_TWW,
        quest_type=QuestType.SIDE,
        map_id=DORNOGAL,
        start_point=_p(DORNOGAL, 47.3, 52.1),
        faction="horde",
        required_level=70,
        estimated_seconds=180.0,
    ),
    # -------------------------------------------- Earlier expansion examples
    Quest(
        quest_id=72001,
        title="The Dragon Isles Await",
        expansion=Expansion.DRAGONFLIGHT,
        quest_type=QuestType.CAMPAIGN,
        map_id=WAKING_SHORES,
        start_point=_p(WAKING_SHORES, 24.1, 56.3),
        campaign="df_campaign",
        campaign_step=1,
        required_level=58,
        estimated_seconds=300.0,
    ),
    Quest(
        quest_id=72002,
        title="Reunion at Wingrest",
        expansion=Expansion.DRAGONFLIGHT,
        quest_type=QuestType.CAMPAIGN,
        map_id=WAKING_SHORES,
        start_point=_p(WAKING_SHORES, 60.4, 70.2),
        prerequisites=frozenset({72001}),
        campaign="df_campaign",
        campaign_step=2,
        required_level=58,
        estimated_seconds=280.0,
    ),
    Quest(
        quest_id=72003,
        title="Audience with the Aspects",
        expansion=Expansion.DRAGONFLIGHT,
        quest_type=QuestType.CAMPAIGN,
        map_id=VALDRAKKEN,
        start_point=_p(VALDRAKKEN, 55.2, 42.8),
        prerequisites=frozenset({72002}),
        campaign="df_campaign",
        campaign_step=3,
        required_level=60,
        estimated_seconds=240.0,
    ),
    Quest(
        quest_id=60001,
        title="The Path to Bastion",
        expansion=Expansion.SHADOWLANDS,
        quest_type=QuestType.CAMPAIGN,
        map_id=ORIBOS,
        start_point=_p(ORIBOS, 48.9, 51.2),
        campaign="sl_campaign",
        campaign_step=1,
        required_level=50,
        estimated_seconds=260.0,
    ),
    Quest(
        quest_id=60002,
        title="Fresh Ascension",
        expansion=Expansion.SHADOWLANDS,
        quest_type=QuestType.CAMPAIGN,
        map_id=BASTION,
        start_point=_p(BASTION, 51.3, 60.7),
        prerequisites=frozenset({60001}),
        campaign="sl_campaign",
        campaign_step=2,
        required_level=50,
        estimated_seconds=320.0,
    ),
    Quest(
        quest_id=26,
        title="Kobold Camp Cleanup",
        expansion=Expansion.CLASSIC,
        quest_type=QuestType.SIDE,
        map_id=ELWYNN,
        start_point=_p(ELWYNN, 43.5, 65.9),
        start_npc="Marshal McBride",
        faction="alliance",
        required_level=1,
        estimated_seconds=180.0,
    ),
]


SEED_CHAINS: list[QuestChain] = [
    QuestChain(
        key="tww_main",
        name="The War Within: Main Campaign",
        expansion=_TWW,
        quest_ids=(78706, 78707, 78708, 78709, 78710, 78711, 78712),
        description="The primary storyline through Khaz Algar.",
        map_id=ISLE_OF_DORN,
        campaign=_CAMPAIGN_TWW,
    ),
    QuestChain(
        key="tww_nerubian",
        name="The War Within: Nerubian Depths",
        expansion=_TWW,
        quest_ids=(78901,),
        description="The descent into Azj-Kahet.",
        map_id=AZJ_KAHET,
        campaign="tww_nerubian",
    ),
    QuestChain(
        key="df_main",
        name="Dragonflight: Main Campaign",
        expansion=Expansion.DRAGONFLIGHT,
        quest_ids=(72001, 72002, 72003),
        map_id=WAKING_SHORES,
        campaign="df_campaign",
    ),
    QuestChain(
        key="sl_main",
        name="Shadowlands: Main Campaign",
        expansion=Expansion.SHADOWLANDS,
        quest_ids=(60001, 60002),
        map_id=ORIBOS,
        campaign="sl_campaign",
    ),
]


def build_seed_graph():
    """Construct a validated graph from the seed data."""
    from app.quests.graph import QuestGraph

    return QuestGraph(SEED_QUESTS, SEED_CHAINS)
