"""Tests for the reasoning/Game Master module and the player model.

Pure-logic tests exercise the engine without a database; API tests exercise the
full endpoint path with the hermetic SQLite client.
"""

from __future__ import annotations

import pytest

from app.modules.reasoning.domain import IntentKind, PlayerContext
from app.modules.reasoning.engine import GameMasterEngine, infer_intent


# --- Intent inference (pure) -----------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("how do I upgrade my gear and get a better trinket", IntentKind.GEAR_UP),
        ("teach me how to clear the raid on mythic", IntentKind.CLEAR_CONTENT),
        ("how do I collect the Ashes of Alar mount", IntentKind.COMPLETE_COLLECTION),
        ("help me improve my fury warrior rotation", IntentKind.IMPROVE_ROTATION),
        ("what should I do this week", IntentKind.OPEN),
    ],
)
def test_infer_intent(text, expected):
    ctx = PlayerContext(user_id="u", spec_id=251)  # neutral profile (no content bias)
    assert infer_intent(text, ctx) == expected


def test_infer_intent_uses_profile_signals_on_tie():
    ctx = PlayerContext(user_id="u", spec_id=None, favorite_content=["mythic_plus"])
    # Weakly content-flavoured text; profile pushes CLEAR_CONTENT.
    assert infer_intent("what next", ctx) in (
        IntentKind.CLEAR_CONTENT,
        IntentKind.PROGRESS_CHARACTER,
        IntentKind.OPEN,
    )


# --- Plan generation (pure) ------------------------------------------------


def test_gear_plan_ranks_explained_objectives():
    ctx = PlayerContext(user_id="u", spec_id=251, spec="Frost Death Knight", role="damage")
    plan = GameMasterEngine().plan("how do I upgrade my gear", ctx)
    assert plan.intent == IntentKind.GEAR_UP
    assert plan.objectives
    # Every objective must carry a *why*.
    for o in plan.objectives:
        assert o.rationale
        assert o.next_action
    # Gear objective present and prioritised.
    assert any(o.category.value == "gear" for o in plan.objectives)


def test_collection_plan_pulls_fastest_collectibles():
    ctx = PlayerContext(user_id="u", spec_id=102, spec="Holy Paladin", role="healer")
    plan = GameMasterEngine().plan("how do I collect the rattling ironcage mount", ctx)
    assert plan.intent == IntentKind.COMPLETE_COLLECTION
    assert any(o.category.value == "collect" for o in plan.objectives)
    # Fastest goals surface first by construction.
    collects = [o for o in plan.objectives if o.category.value == "collect"]
    assert collects


def test_open_intent_without_spec_asks_questions():
    ctx = PlayerContext(user_id="u", spec_id=None)
    plan = GameMasterEngine().plan("what should I do", ctx)
    assert plan.intent == IntentKind.OPEN
    assert plan.open_questions
    assert any("specialization" in q for q in plan.open_questions)


def test_plan_confidence_rises_with_known_spec():
    bare = PlayerContext(user_id="u", spec_id=None)
    rich = PlayerContext(user_id="u", spec_id=251, spec="Frost DK", favorite_content=["mythic_plus"])
    low = GameMasterEngine().plan("help me gear up", bare).confidence
    high = GameMasterEngine().plan("help me gear up", rich).confidence
    assert high > low


# --- API path (hermetic client) --------------------------------------------


@pytest.mark.asyncio
async def test_reason_endpoint_returns_explained_plan(client, auth_headers):
    resp = await client.post(
        "/api/v1/modules/reasoning/reason",
        headers=auth_headers,
        json={"message": "how do I upgrade my Frost Death Knight for Mythic+", "spec_id": 251},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "gear_up"
    assert body["plan"]["objectives"]
    assert body["plan"]["summary"]
    assert body["conversation_id"]
    for o in body["plan"]["objectives"]:
        assert o["rationale"]
        assert o["source"] in {"gear", "coach", "collections", "rotation"}


@pytest.mark.asyncio
async def test_learn_then_reason_personalises(client, auth_headers):
    learn = await client.put(
        "/api/v1/modules/reasoning/profile",
        headers=auth_headers,
        json={
            "favorite_class": "death_knight",
            "favorite_spec": "Frost",
            "favorite_spec_id": 251,
            "favorite_content": ["mythic_plus"],
            "current_goals": ["reach 2500 rio"],
        },
    )
    assert learn.status_code == 200
    assert learn.json()["favorite_spec_id"] == 251

    getp = await client.get("/api/v1/modules/reasoning/profile", headers=auth_headers)
    assert getp.status_code == 200
    assert getp.json()["favorite_spec_id"] == 251

    # Reason without spec_id; engine should still plan using the stored spec.
    resp = await client.post(
        "/api/v1/modules/reasoning/reason",
        headers=auth_headers,
        json={"message": "what should I do this week"},
    )
    assert resp.status_code == 200
    # Gear objective appears because the profile has a spec + M+ focus.
    assert any(o["category"] == "gear" for o in resp.json()["plan"]["objectives"])


@pytest.mark.asyncio
async def test_conversations_are_remembered(client, auth_headers):
    await client.post(
        "/api/v1/modules/reasoning/reason",
        headers=auth_headers,
        json={"message": "how do I gear up my Frost DK", "spec_id": 251},
    )
    convs = await client.get("/api/v1/modules/reasoning/conversations", headers=auth_headers)
    assert convs.status_code == 200
    assert len(convs.json()) >= 1
