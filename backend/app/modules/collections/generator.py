"""Collection Tracker generator + Blizzard collections enrichment.

Recommends the fastest-obtainable collectibles for a character, estimates the
time to finish a category or the whole tracker, and filters by class/spec
(notably Druid forms). Live Blizzard counts can be layered on top via
:class:`CollectionsClient`; the seed catalog makes everything work offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.collections.domain import (
    CLASS_BY_SPEC,
    Collectible,
    CollectionCategory,
    CollectionProfile,
    catalog_by_category,
)


@dataclass
class CollectionGoal:
    id: str
    name: str
    category: str
    obtain: str
    minutes: float
    notes: str = ""


@dataclass
class CollectionPlan:
    spec_id: int
    wow_class: str | None
    categories: list[str]
    fastest_goals: list[CollectionGoal]
    per_category_eta_min: dict[str, float]
    total_eta_min: float
    estimated_total_min: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "wow_class": self.wow_class,
            "categories": self.categories,
            "fastest_goals": [vars(g) for g in self.fastest_goals],
            "per_category_eta_min": {
                k: round(v, 1) for k, v in self.per_category_eta_min.items()
            },
            "total_eta_min": round(self.total_eta_min, 1),
            "estimated_total_min": round(self.estimated_total_min, 1),
            "notes": self.notes,
        }


def _class_for(spec_id: int) -> str | None:
    return CLASS_BY_SPEC.get(spec_id)


def _visible(collectible: Collectible, wow_class: str | None) -> bool:
    """A collectible is visible to a class if it has no class gate, or the class
    matches. Druid forms only show for Druids, etc."""
    if not collectible.classes:
        return True
    return wow_class in collectible.classes


def build_plan(
    spec_id: int,
    owned: CollectionProfile | None = None,
    categories: list[CollectionCategory] | None = None,
) -> CollectionPlan:
    wow_class = _class_for(spec_id)
    cats = categories or list(CollectionCategory)
    cat_values: list[str] = []

    fastest: list[CollectionGoal] = []
    per_cat_eta: dict[str, float] = {}
    grand_total = 0.0

    for cat in cats:
        items = [i for i in catalog_by_category(cat) if _visible(i, wow_class)]
        if not items:
            # Every collectible in this category is gated to another class
            # (e.g. druid_forms for a non-Druid); skip it entirely.
            continue
        cat_values.append(cat.value)
        owned_ids = owned.owned_ids(cat) if owned else set()
        remaining = [i for i in items if i.id not in owned_ids]

        # Fastest obtainable: sort by minutes, take a few quick wins.
        for i in sorted(remaining, key=lambda x: x.minutes)[:3]:
            fastest.append(
                CollectionGoal(
                    id=i.id,
                    name=i.name,
                    category=i.category.value,
                    obtain=i.obtain.value,
                    minutes=i.minutes,
                    notes=i.notes,
                )
            )
        cat_total = sum(i.minutes for i in remaining)
        per_cat_eta[cat.value] = cat_total
        grand_total += cat_total

    fastest.sort(key=lambda g: g.minutes)
    notes = []
    if wow_class == "Druid":
        notes.append("Druid forms are gated to your class — tracked separately.")
    if not owned:
        notes.append("No owned data supplied; ETA assumes nothing collected yet.")

    return CollectionPlan(
        spec_id=spec_id,
        wow_class=wow_class,
        categories=cat_values,
        fastest_goals=fastest,
        per_category_eta_min=per_cat_eta,
        total_eta_min=grand_total,
        estimated_total_min=grand_total,
        notes=notes,
    )
