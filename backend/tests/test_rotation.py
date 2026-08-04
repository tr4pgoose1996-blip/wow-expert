"""Tests for the rotation advisor engine and endpoints.

Hermetic: SQLite engine + FakeRedis via the shared conftest, so no external
services are required. The engine is exercised directly (pure logic) and
through the HTTP surface with an authenticated client.
"""

from __future__ import annotations

import pytest

from app.modules.rotation.engine import AdviceKind, RotationAdvisor
from app.modules.rotation.schemas import AdviceKind as SchemaAdvice
from app.modules.rotation.specs import (
    SPECIALIZATIONS,
    SPEC_BY_ID,
    CombatState,
    ResourceType,
)
from app.modules.rotation.service import RotationService


# ---------------------------------------------------------------------------
# Engine: priority system, AoE/ST, movement, execute, cooldowns
# ---------------------------------------------------------------------------


def test_all_40_specializations_catalogued() -> None:
    # 13 classes; every playable spec including DH Devourer (1480).
    assert len(SPECIALIZATIONS) == 40
    assert SPECIALIZATIONS[0].spec_id == 250  # Blood DK
    assert SPECIALIZATIONS[-1].spec_id == 73  # Protection Warrior
    assert any(s.spec_id == 1480 and s.name == "Devourer" for s in SPECIALIZATIONS)


@pytest.mark.parametrize(
    "spec_id,expected_top",
    [
        (251, "Obliterate"),      # Frost DK single target top spender
        (577, "Chaos Strike"),    # Havoc ST
        (102, "Wrath"),           # Balance
        (265, "Darkglare"),       # Afflic Warlock burst (priority 5)
    ],
)
def test_single_target_priority(spec_id: int, expected_top: str) -> None:
    advisor = RotationAdvisor()
    state = CombatState(spec_id=spec_id, resource=100, resource_max=100)
    advice = advisor.advise(spec_id, state)
    assert advice.suggestion is not None
    assert advice.suggestion.name == expected_top
    assert advice.situation == "single_target"


def test_aoe_uses_aoe_priority() -> None:
    advisor = RotationAdvisor()
    # Balance AoE: without the Euphoria buff active, the highest-priority
    # eligible AoE ability is Sunfire (Starfall is correctly gated by its
    # required buff).
    state = CombatState(spec_id=102, targets=4, resource=100, resource_max=100)
    advice = advisor.advise(102, state)
    assert advice.situation == "aoe"
    assert advice.suggestion is not None
    assert advice.suggestion.name == "Sunfire"


def test_movement_adaptation_only_mobile_abilities() -> None:
    advisor = RotationAdvisor()
    # Frost DK moving with no castable-while-moving ability eligible besides
    # Glacial Advance (mobile). Ensure no long-cast filler is suggested.
    state = CombatState(spec_id=251, targets=1, moving=True, resource=100,
                         resource_max=100)
    advice = advisor.advise(251, state)
    if advice.suggestion:
        # Glacial Advance is the mobile ST filler; Howling Blast is also mobile.
        assert advice.suggestion.name in {"Glacial Advance", "Howling Blast"}


def test_execute_phase_low_target_health() -> None:
    advisor = RotationAdvisor()
    # Frost DK has execute_below_pct=35; at 20% health the situation is flagged.
    state = CombatState(spec_id=251, target_health_pct=20, resource=100,
                         resource_max=100)
    advice = advisor.advise(251, state)
    assert "execute" in advice.situation


def test_cooldown_plan_reports_ready_and_on_cd() -> None:
    advisor = RotationAdvisor()
    state = CombatState(
        spec_id=251, resource=100, resource_max=100,
        cooldowns_remaining={"Pillar of Frost": 0, "Empower Rune Weapon": 30},
    )
    plan = advisor.cooldown_plan(251, state)
    assert "Pillar of Frost" in plan["ready"]
    assert any(c["name"] == "Empower Rune Weapon" for c in plan["on_cooldown"])


def test_resource_starvation_warns() -> None:
    advisor = RotationAdvisor()
    # Frost DK with no runic power: Obliterate (cost 2) unavailable.
    state = CombatState(spec_id=251, resource=0, resource_max=100, starved=True)
    advice = advisor.advise(251, state)
    assert any("starved" in w.lower() for w in advice.warnings)


def test_talent_awareness_missing_talent() -> None:
    advisor = RotationAdvisor(active_talents=set())  # no talents known
    state = CombatState(spec_id=251)
    advice = advisor.advise(251, state)
    # Profile assumes talents; with none active, hints list them.
    assert any("Obliteration" in h for h in advice.talent_hints)


def test_talent_awareness_match() -> None:
    advisor = RotationAdvisor(active_talents={"Obliteration", "Gathering Storm",
                                              "Breath of Sindragosa"})
    state = CombatState(spec_id=251)
    advice = advisor.advise(251, state)
    assert any("match" in h.lower() for h in advice.talent_hints)


def test_static_rotation_views() -> None:
    advisor = RotationAdvisor()
    for situation in (AdviceKind.OPENER, AdviceKind.SINGLE_TARGET,
                      AdviceKind.AOE, AdviceKind.MOVEMENT, AdviceKind.DEFENSIVE):
        out = advisor.rotation_for(251, situation)
        assert out, f"no abilities for {situation}"
        # Sorted by priority ascending.
        priorities = [a.priority for a in out]
        assert priorities == sorted(priorities)


def test_defensive_plan_returned_for_tank() -> None:
    advisor = RotationAdvisor()
    state = CombatState(spec_id=250)  # Blood DK
    advice = advisor.advise(250, state)
    assert advice.defensives, "tanks should surface defensives"
    assert "Icebound Fortitude" in advice.defensives


def test_unknown_spec_raises() -> None:
    advisor = RotationAdvisor()
    with pytest.raises(Exception):
        advisor.advise(999999, CombatState(spec_id=999999))


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_list_and_profile() -> None:
    svc = RotationService()
    specs = svc.list_specializations()
    assert len(specs) == 40
    prof = svc.get_profile(251)
    assert prof.spec_name == "Frost"
    assert prof.resource == ResourceType.RUNIC_POWER


@pytest.mark.asyncio
async def test_service_advise_next() -> None:
    svc = RotationService()
    from app.modules.rotation.schemas import RotationRequest

    advice = svc.advise(RotationRequest(spec_id=251, situation=SchemaAdvice.NEXT))
    assert advice.suggestion is not None


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_specs_requires_auth(client: object) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    resp = await c.get("/api/v1/modules/rotation")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_specs_authed(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    resp = await c.get("/api/v1/modules/rotation", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 40
    ids = {s["spec_id"] for s in data["specializations"]}
    assert 250 in ids and 1480 in ids and 73 in ids and len(ids) == 40


@pytest.mark.asyncio
async def test_profile_endpoint(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    resp = await c.get("/api/v1/modules/rotation/251", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["spec_name"] == "Frost"
    assert data["role"] == "damage"
    assert data["resource"] == "runic_power"


@pytest.mark.asyncio
async def test_profile_unknown_404(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    resp = await c.get("/api/v1/modules/rotation/999999", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_advise_next_endpoint(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    body = {
        "spec_id": 251,
        "situation": "next",
        "state": {
            "targets": 2,
            "moving": True,
            "resource": 85,
            "resource_max": 100,
            "cooldowns_remaining": {"Pillar of Frost": 0},
            "active_talents": ["Obliteration", "Gathering Storm", "Breath of Sindragosa"],
        },
    }
    resp = await c.post(
        "/api/v1/modules/rotation/advise", json=body, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["situation"] == "movement"  # moving takes priority over aoe
    assert data["suggestion"] is not None
    assert "Pillar of Frost" in data["ready_cooldowns"]


@pytest.mark.asyncio
async def test_rotation_teaching_view(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    resp = await c.get(
        "/api/v1/modules/rotation/102/rotation?situation=aoe", headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and len(data) > 0
    assert data[0]["name"] == "Starfall"


@pytest.mark.asyncio
async def test_advise_validation_error(client: object, auth_headers: dict) -> None:
    from httpx import AsyncClient

    c = client  # type: ignore[assignment]
    # Missing required spec_id.
    resp = await c.post(
        "/api/v1/modules/rotation/advise", json={"situation": "next"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
