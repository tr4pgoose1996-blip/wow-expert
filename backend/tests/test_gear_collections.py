"""Tests for the Gear Advisor and Collection Tracker modules.

Hermetic: engines are pure, services are stateless, and Blizzard clients are not
instantiated (so no network/credentials are needed).
"""

from __future__ import annotations

import pytest

from app.modules.gear.domain import (
    GearItem,
    GearRarity,
    ItemSlot,
    ItemStats,
    Stat,
    weights_for_spec,
)
from app.modules.gear.engine import GearAdvisor
from app.modules.gear.schemas import CompareRequest, GearItemIn, RecommendRequest
from app.modules.gear.service import GearAdvisorService
from app.modules.collections.domain import (
    CollectionCategory,
    CollectionProfile,
    CLASS_BY_SPEC,
)
from app.modules.collections.generator import build_plan


# ---------------------------------------------------------------------------
# Gear Advisor: engine
# ---------------------------------------------------------------------------


def _item(slot: str, name: str, stats: dict[Stat, float], **kw) -> GearItem:
    return GearItem(
        slot=ItemSlot(slot), name=name,
        stats=ItemStats(values=stats), **kw,
    )


def test_weights_per_spec_differ() -> None:
    frost = weights_for_spec(251, "damage")
    affl = weights_for_spec(265, "damage")
    # Frost favours crit; Affliction favours haste (override present).
    assert frost[Stat.CRIT] >= frost[Stat.HASTE]
    assert affl[Stat.HASTE] > affl[Stat.CRIT]


def test_simulate_aggregates_stats() -> None:
    adv = GearAdvisor(weights_for_spec(251, "damage"))
    items = [
        _item("head", "H", {Stat.STRENGTH: 100, Stat.CRIT: 50}),
        _item("chest", "C", {Stat.STRENGTH: 120, Stat.HASTE: 40}),
    ]
    totals = adv.simulate(items)
    assert totals[Stat.STRENGTH] == 220
    assert totals[Stat.CRIT] == 50
    assert totals[Stat.HASTE] == 40


def test_compare_upgrade_detected() -> None:
    adv = GearAdvisor(weights_for_spec(251, "damage"))
    cur = _item("head", "Old", {Stat.STRENGTH: 50, Stat.CRIT: 10})
    new = _item("head", "New", {Stat.STRENGTH: 80, Stat.CRIT: 40})
    cmp = adv.compare(ItemSlot.HEAD, cur, new)
    assert cmp.is_upgrade is True
    assert cmp.delta > 0


def test_compare_downgrade_rejected() -> None:
    adv = GearAdvisor(weights_for_spec(251, "damage"))
    cur = _item("head", "Good", {Stat.STRENGTH: 200})
    new = _item("head", "Bad", {Stat.STRENGTH: 50})
    cmp = adv.compare(ItemSlot.HEAD, cur, new)
    assert cmp.is_upgrade is False


def test_rank_upgrades_picks_best_per_slot() -> None:
    adv = GearAdvisor(weights_for_spec(251, "damage"))
    equipped = {ItemSlot.HEAD: _item("head", "Equipped", {Stat.STRENGTH: 100})}
    candidates = [
        _item("head", "Worse", {Stat.STRENGTH: 60}),
        _item("head", "Better", {Stat.STRENGTH: 150}),
        _item("chest", "ChestDrop", {Stat.STRENGTH: 90}),
    ]
    ranked = adv.rank_upgrades(equipped, candidates)
    head = next(r for r in ranked if r.slot == ItemSlot.HEAD)
    assert head.candidate.name == "Better"
    assert head.is_upgrade is True


def test_recommend_generic_fills_empty_slots() -> None:
    adv = GearAdvisor()
    rec = adv.recommend(251, "damage", equipped={})
    assert rec.simulated_score == 0.0
    assert len(rec.upgrades) == len(ItemSlot)  # all empty
    assert rec.enchant_recommendations
    assert rec.gem_recommendations
    assert rec.trinket_recommendations
    assert rec.vault_priority


def test_recommend_with_candidates() -> None:
    adv = GearAdvisor()
    equipped = {ItemSlot.HEAD: _item("head", "Eq", {Stat.STRENGTH: 100})}
    candidates = [_item("head", "Up", {Stat.STRENGTH: 180})]
    rec = adv.recommend(251, "damage", equipped, candidates)
    assert any(u.candidate.name == "Up" and u.is_upgrade for u in rec.upgrades)


def test_trinket_recommendations_role_scoped() -> None:
    adv = GearAdvisor()
    tank = adv.recommend(250, "tank", equipped={})
    dps = adv.recommend(251, "damage", equipped={})
    tank_names = {t["name"] for t in tank.trinket_recommendations}
    dps_names = {t["name"] for t in dps.trinket_recommendations}
    assert tank_names != dps_names  # roles get different trinket tiers


# ---------------------------------------------------------------------------
# Gear Advisor: service + HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gear_advise_endpoint(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    body = {
        "spec_id": 251,
        "role": "damage",
        "equipped": [
            {"slot": "head", "name": "Eq", "stats": {"strength": 100}},
        ],
        "candidates": [
            {"slot": "head", "name": "Up", "stats": {"strength": 180, "crit": 60}},
        ],
    }
    resp = await c.post("/api/v1/modules/gear/advise", json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["spec_id"] == 251
    assert any(u["candidate"] == "Up" and u["is_upgrade"] for u in data["upgrades"])
    assert data["enchant_recommendations"]
    assert data["vault_priority"]


@pytest.mark.asyncio
async def test_gear_compare_endpoint(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    body = {
        "spec_id": 251,
        "role": "damage",
        "slot": "chest",
        "current": {"slot": "chest", "name": "Old", "stats": {"strength": 50}},
        "candidate": {"slot": "chest", "name": "New", "stats": {"strength": 200}},
    }
    resp = await c.post("/api/v1/modules/gear/compare", json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_upgrade"] is True


@pytest.mark.asyncio
async def test_gear_requires_auth(client: object) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    resp = await c.post("/api/v1/modules/gear/advise", json={"spec_id": 251})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Collection Tracker: generator + service + HTTP
# ---------------------------------------------------------------------------


def test_class_for_spec() -> None:
    assert CLASS_BY_SPEC[102] == "Druid"
    assert CLASS_BY_SPEC[253] == "Hunter"


def test_build_plan_druid_gates_forms() -> None:
    plan = build_plan(102)  # Druid
    cats = set(plan.categories)
    assert "druid_forms" in cats
    form_goals = [g for g in plan.fastest_goals if g.category == "druid_forms"]
    assert form_goals  # Druid sees druid-form goals


def test_build_plan_non_druid_excludes_forms() -> None:
    plan = build_plan(251)  # Frost DK
    assert "druid_forms" not in plan.categories
    form_goals = [g for g in plan.fastest_goals if g.category == "druid_forms"]
    assert not form_goals


def test_build_plan_fastest_sorted() -> None:
    plan = build_plan(253)  # Hunter
    minutes = [g.minutes for g in plan.fastest_goals]
    assert minutes == sorted(minutes)


def test_build_plan_owned_reduces_eta() -> None:
    full = build_plan(251)
    owned = CollectionProfile()
    owned.owned[CollectionCategory.MOUNTS] = {"mount_skyride", "mount_raid", "mount_mplus", "mount_rep"}
    partial = build_plan(251, owned)
    assert partial.per_category_eta_min["mounts"] < full.per_category_eta_min["mounts"]


def test_build_plan_category_filter() -> None:
    plan = build_plan(251, categories=[CollectionCategory.TOYS])
    assert plan.categories == ["toys"]


@pytest.mark.asyncio
async def test_collections_plan_endpoint(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    body = {"spec_id": 102, "categories": ["mounts", "druid_forms"]}
    resp = await c.post("/api/v1/modules/collections/plan", json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["wow_class"] == "Druid"
    assert "druid_forms" in data["categories"]
    assert data["fastest_goals"]
    assert data["total_eta_min"] > 0


@pytest.mark.asyncio
async def test_collections_counts_endpoint(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    resp = await c.get("/api/v1/modules/collections/counts", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)  # empty without Blizzard creds


@pytest.mark.asyncio
async def test_collections_requires_auth(client: object) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    resp = await c.post("/api/v1/modules/collections/plan", json={"spec_id": 251})
    assert resp.status_code == 401
