"""Gear Advisor service: maps API requests to the engine."""

from __future__ import annotations

from typing import Any

from app.core.exceptions import ValidationError
from app.modules.gear.domain import (
    GearItem,
    GearRarity,
    ItemSlot,
    ItemStats,
    Stat,
    weights_for_spec,
)
from app.modules.gear.engine import GearAdvisor, GearRecommendation
from app.modules.gear.schemas import CompareRequest, GearItemIn, RecommendRequest


def _to_item(d: GearItemIn) -> GearItem:
    return GearItem(
        slot=ItemSlot(d.slot),
        name=d.name,
        item_id=d.item_id,
        item_level=d.item_level,
        rarity=GearRarity(d.rarity),
        stats=ItemStats(values={Stat(k): v for k, v in d.stats.items()}),
        is_crafted=d.is_crafted,
        is_trinket=d.is_trinket,
        source=d.source,
    )


class GearAdvisorService:
    """Stateless service wrapping :class:`GearAdvisor`."""

    def recommend(self, request: RecommendRequest) -> GearRecommendation:
        equipped = {_to_item(i).slot: _to_item(i) for i in request.equipped}
        candidates = [_to_item(i) for i in request.candidates] or None
        advisor = GearAdvisor(weights_for_spec(request.spec_id, request.role))
        return advisor.recommend(
            spec_id=request.spec_id,
            role=request.role,
            equipped=equipped,
            candidates=candidates,
        )

    def compare(self, request: CompareRequest) -> dict[str, Any]:
        current = _to_item(request.current) if request.current else None
        candidate = _to_item(request.candidate)
        try:
            slot = ItemSlot(request.slot)
        except ValueError as exc:
            raise ValidationError(
                f"Unknown slot '{request.slot}'.",
                details={"valid": [s.value for s in ItemSlot]},
            ) from exc
        advisor = GearAdvisor(weights_for_spec(request.spec_id, request.role))
        cmp = advisor.compare(slot, current, candidate)
        return {
            "slot": slot.value,
            "current": current.name if current else None,
            "candidate": candidate.name,
            "current_score": cmp.current_score,
            "candidate_score": cmp.candidate_score,
            "delta": cmp.delta,
            "is_upgrade": cmp.is_upgrade,
        }
