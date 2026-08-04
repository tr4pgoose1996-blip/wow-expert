"""Tests for the Combat Coach: domain, generator, service, and HTTP surface.

Hermetic: no Blizzard calls are made (Journal enrichment is skipped without a
client), so the suite runs offline against the structured seed data and the
pure teaching generator.
"""

from __future__ import annotations

import pytest

from app.modules.coach.domain import (
    Affix,
    Difficulty,
    INSTANCE_BY_ID,
    Role,
)
from app.modules.coach.generator import generate_briefing
from app.modules.coach.schemas import CoachBriefingRequest
from app.modules.coach.service import CombatCoachService


# ---------------------------------------------------------------------------
# Domain sanity
# ---------------------------------------------------------------------------


def test_instances_present() -> None:
    from app.modules.coach.domain import INSTANCES

    assert len(INSTANCES) >= 3
    assert any(i.content_type.value == "dungeon" for i in INSTANCES)
    assert any(i.content_type.value == "raid" for i in INSTANCES)


def test_boss_lookup() -> None:
    svc = CombatCoachService()
    inst = svc.get_instance(0)  # Example Dungeon A
    assert inst.name
    boss = svc.get_boss(0, 1)
    assert boss[1].name == "First Boss"


def test_unknown_instance_404() -> None:
    svc = CombatCoachService()
    from app.core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        svc.get_instance(99999)


# ---------------------------------------------------------------------------
# Generator: covers every role / difficulty / affix combination
# ---------------------------------------------------------------------------


def _brief(role: Role, diff: Difficulty, affixes=()) -> dict:
    svc = CombatCoachService()
    inst = svc.get_instance(0)
    boss = inst.bosses[0]
    req = CoachBriefingRequest(role=role, difficulty=diff, affixes=list(affixes))
    return svc.get_boss(0, boss.journal_id) and generate_briefing(
        inst, boss, role, diff, tuple(affixes)
    ).to_dict()


def test_tank_heroic_briefing_has_defensives() -> None:
    b = _brief(Role.TANK, Difficulty.HEROIC)
    cats = {p["category"] for p in b["briefing"]}
    assert "defensive_planning" in cats
    assert "positioning" in cats


def test_healer_briefing_has_defensive_planning() -> None:
    b = _brief(Role.HEALER, Difficulty.MYTHIC)
    cats = {p["category"] for p in b["briefing"]}
    assert "defensive_planning" in cats
    assert "awareness" in cats


def test_dps_briefing_has_burst_and_interrupts() -> None:
    b = _brief(Role.DPS, Difficulty.HEROIC)
    cats = {p["category"] for p in b["briefing"]}
    assert "burst_windows" in cats
    assert "interrupts" in cats


def test_mythic_plus_includes_affix_coaching() -> None:
    b = _brief(Role.DPS, Difficulty.MYTHIC_PLUS, affixes=(Affix.FORTIFIED, Affix.STORMING))
    affix_points = [p for p in b["briefing"] if p["category"] == "affix"]
    assert len(affix_points) >= 2
    assert b["difficulty"] == "mythic_plus"
    assert "fortified" in b["affixes"]


def test_difficulty_scaling_increases_severity() -> None:
    normal = _brief(Role.DPS, Difficulty.NORMAL)
    mythic = _brief(Role.DPS, Difficulty.MYTHIC)
    normal_max = max(
        (p["severity"] for p in normal["briefing"]), default="info"
    )
    mythic_max = max(
        (p["severity"] for p in mythic["briefing"]), default="info"
    )
    rank = {"info": 0, "important": 1, "critical": 2}
    assert rank[mythic_max] >= rank[normal_max]


def test_common_mistakes_surfaced() -> None:
    b = _brief(Role.DPS, Difficulty.HEROIC)
    assert len(b["common_mistakes"]) >= 1


def test_briefing_not_empty_for_every_role_difficulty() -> None:
    inst = CombatCoachService().get_instance(0)
    for role in Role:
        for diff in Difficulty:
            boss = inst.bosses[0]
            b = generate_briefing(inst, boss, role, diff).to_dict()
            assert len(b["briefing"]) >= 3, (role, diff)


# ---------------------------------------------------------------------------
# Service + HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_instances_authed(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    resp = await c.get("/api/v1/modules/coach", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and len(data) >= 3
    assert any(i["content_type"] == "dungeon" for i in data)


@pytest.mark.asyncio
async def test_list_bosses_authed(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    resp = await c.get("/api/v1/modules/coach/0/bosses", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["name"] == "First Boss"


@pytest.mark.asyncio
async def test_brief_endpoint_tank_heroic(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    body = {"role": "tank", "difficulty": "heroic", "affixes": []}
    resp = await c.post(
        "/api/v1/modules/coach/0/boss/1/brief",
        json=body,
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["boss_name"] == "First Boss"
    assert data["role"] == "tank"
    assert data["difficulty"] == "heroic"
    cats = {p["category"] for p in data["briefing"]}
    assert "defensive_planning" in cats


@pytest.mark.asyncio
async def test_brief_endpoint_mythic_plus_affixes(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    body = {
        "role": "dps",
        "difficulty": "mythic_plus",
        "affixes": ["fortified", "storming", "explosive"],
    }
    resp = await c.post(
        "/api/v1/modules/coach/0/boss/2/brief",
        json=body,
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["difficulty"] == "mythic_plus"
    assert set(data["affixes"]) == {"fortified", "storming", "explosive"}
    assert any(p["category"] == "affix" for p in data["briefing"])


@pytest.mark.asyncio
async def test_brief_unknown_boss_404(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    body = {"role": "dps", "difficulty": "heroic"}
    resp = await c.post(
        "/api/v1/modules/coach/0/boss/99999/brief",
        json=body,
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_coach_requires_auth(client: object) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    resp = await c.get("/api/v1/modules/coach")
    assert resp.status_code == 401
