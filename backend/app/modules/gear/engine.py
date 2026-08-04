"""Gear Advisor engine: simulate stats, compare upgrades, recommend optimisations.

Pure and stateless — given a character's current gear, a target specialization,
and (optionally) candidate items, it scores upgrades, fills empty slots, and
produces enchant/gem/trinket/crafted/vault recommendations. Works for every
class and specialization via the shared stat-weight tables.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.modules.gear.domain import (
    CRAFTABLE_SLOTS,
    TRINKET_TIERS,
    VAULT_PRIORITY_SLOTS,
    GearItem,
    ItemSlot,
    Stat,
    weights_for_spec,
)


@dataclass
class UpgradeComparison:
    slot: ItemSlot
    current: GearItem | None
    candidate: GearItem
    current_score: float
    candidate_score: float
    delta: float
    is_upgrade: bool


@dataclass
class GearRecommendation:
    spec_id: int
    role: str
    simulated_stats: dict[str, float] = field(default_factory=dict)
    simulated_score: float = 0.0
    upgrades: list[UpgradeComparison] = field(default_factory=list)
    enchant_recommendations: list[dict[str, Any]] = field(default_factory=list)
    gem_recommendations: list[dict[str, Any]] = field(default_factory=list)
    trinket_recommendations: list[dict[str, Any]] = field(default_factory=list)
    crafted_recommendations: list[dict[str, Any]] = field(default_factory=list)
    vault_priority: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "role": self.role,
            "simulated_stats": self.simulated_stats,
            "simulated_score": round(self.simulated_score, 2),
            "upgrades": [
                {
                    "slot": u.slot.value,
                    "current": u.current.name if u.current else None,
                    "candidate": u.candidate.name,
                    "delta": round(u.delta, 2),
                    "is_upgrade": u.is_upgrade,
                }
                for u in self.upgrades
            ],
            "enchant_recommendations": self.enchant_recommendations,
            "gem_recommendations": self.gem_recommendations,
            "trinket_recommendations": self.trinket_recommendations,
            "crafted_recommendations": self.crafted_recommendations,
            "vault_priority": self.vault_priority,
            "notes": self.notes,
        }


class GearAdvisor:
    """Score and recommend gear for a specialization."""

    def __init__(self, weights: dict[Stat, float] | None = None) -> None:
        self._weights = weights or {}

    # -- Simulation ---------------------------------------------------------

    def simulate(self, items: Iterable[GearItem]) -> dict[Stat, float]:
        """Aggregate secondary/primary stats across equipped items."""
        totals: dict[Stat, float] = {}
        for item in items:
            for stat, amount in item.stats.values.items():
                totals[stat] = totals.get(stat, 0.0) + amount
        return totals

    def score(self, item: GearItem) -> float:
        return item.stats.score(self._weights)

    def score_stats(self, stats: dict[Stat, float]) -> float:
        return sum(
            amount * self._weights.get(stat, 0.0) for stat, amount in stats.items()
        )

    # -- Comparison ---------------------------------------------------------

    def compare(
        self, slot: ItemSlot, current: GearItem | None, candidate: GearItem
    ) -> UpgradeComparison:
        cur_score = self.score(current) if current else 0.0
        cand_score = self.score(candidate)
        delta = cand_score - cur_score
        return UpgradeComparison(
            slot=slot,
            current=current,
            candidate=candidate,
            current_score=round(cur_score, 2),
            candidate_score=round(cand_score, 2),
            delta=round(delta, 2),
            is_upgrade=delta > 0.0,
        )

    def rank_upgrades(
        self, equipped: dict[ItemSlot, GearItem], candidates: Iterable[GearItem]
    ) -> list[UpgradeComparison]:
        """Compare each candidate against whatever is equipped in its slot."""
        grouped: dict[ItemSlot, list[GearItem]] = {}
        for c in candidates:
            grouped.setdefault(c.slot, []).append(c)
        out: list[UpgradeComparison] = []
        for slot, items in grouped.items():
            current = equipped.get(slot)
            best = max(items, key=self.score)
            out.append(self.compare(slot, current, best))
        return sorted(out, key=lambda u: u.delta, reverse=True)

    # -- Recommendations ----------------------------------------------------

    def recommend(
        self,
        spec_id: int,
        role: str,
        equipped: dict[ItemSlot, GearItem],
        candidates: Iterable[GearItem] | None = None,
    ) -> GearRecommendation:
        weights = self._weights or weights_for_spec(spec_id, role)
        self._weights = weights
        advisor = GearAdvisor(weights)

        sim_stats = advisor.simulate(equipped.values())
        sim_score = advisor.score_stats(sim_stats)

        rec = GearRecommendation(
            spec_id=spec_id,
            role=role,
            simulated_stats={s.value: round(v, 1) for s, v in sim_stats.items()},
            simulated_score=round(sim_score, 2),
        )

        # 1. Upgrades from candidates, or empty-slot detection.
        if candidates:
            rec.upgrades = advisor.rank_upgrades(equipped, candidates)
        else:
            rec.upgrades = [
                UpgradeComparison(
                    slot=slot,
                    current=None,
                    candidate=GearItem(slot, "(empty)"),
                    current_score=0.0,
                    candidate_score=0.0,
                    delta=0.0,
                    is_upgrade=False,
                )
                for slot in ItemSlot
                if slot not in equipped
            ]

        # 2. Enchants, gems, trinkets, crafted, vault.
        rec.enchant_recommendations = _enchant_recs(role, weights)
        rec.gem_recommendations = _gem_recs(weights)
        rec.trinket_recommendations = [
            {"name": name, "desirability": d, "why": why}
            for name, d, why in TRINKET_TIERS.get(role, TRINKET_TIERS["damage"])
        ]
        rec.crafted_recommendations = [
            {"slot": s.value, "reason": _craft_reason(s, role)}
            for s in CRAFTABLE_SLOTS
            if s not in equipped
        ]
        rec.vault_priority = [s.value for s in VAULT_PRIORITY_SLOTS]

        if not equipped:
            rec.notes.append(
                "No equipped gear provided; recommendations are generic for the spec."
            )
        return rec


def _enchant_recs(role: str, weights: dict[Stat, float]) -> list[dict[str, Any]]:
    from app.modules.gear.domain import ENCHANT_RULES

    recs: list[dict[str, Any]] = []
    used_slots: set[ItemSlot] = set()
    for rule in ENCHANT_RULES:
        if rule.slot in used_slots:
            continue
        used_slots.add(rule.slot)
        weight = weights.get(rule.stat, 0.0)
        recs.append(
            {
                "slot": rule.slot.value,
                "enchant": rule.name,
                "stat": rule.stat.value,
                "value": rule.value,
                "priority": round(weight, 2),
                "note": rule.note,
            }
        )
    recs.sort(key=lambda r: r["priority"], reverse=True)
    return recs


def _gem_recs(weights: dict[Stat, float]) -> list[dict[str, Any]]:
    from app.modules.gear.domain import GEM_RULES

    return [
        {
            "color": g.color,
            "stat": g.stat.value,
            "value": g.value,
            "priority": round(weights.get(g.stat, 0.0), 2),
            "note": g.note,
        }
        for g in sorted(GEM_RULES, key=lambda x: weights.get(x.stat, 0.0), reverse=True)
    ]


def _craft_reason(slot: ItemSlot, role: str) -> str:
    if slot in (ItemSlot.NECK, ItemSlot.BACK, ItemSlot.FINGER_1, ItemSlot.FINGER_2):
        return (
            "Craft for a free secondary-stat missive and a guaranteed high item level."
        )
    if role == "tank" and slot in (ItemSlot.CHEST, ItemSlot.LEGS):
        return "Craft for Stamina to shore up effective health."
    return "Craft with a missive targeting your highest-weight secondary."
