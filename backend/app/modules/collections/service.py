"""Collection Tracker service: maps API requests to the generator/client."""

from __future__ import annotations

from typing import Any

from app.core.exceptions import ValidationError
from app.modules.collections.client import CollectionsClient
from app.modules.collections.domain import (
    CollectionCategory,
    CollectionProfile,
)
from app.modules.collections.generator import build_plan
from app.modules.collections.schemas import PlanRequest


def _parse_owned(raw: dict[str, list[str]] | None) -> CollectionProfile:
    profile = CollectionProfile()
    if not raw:
        return profile
    for key, ids in raw.items():
        try:
            cat = CollectionCategory(key)
        except ValueError:
            raise ValidationError(
                f"Unknown collection category '{key}'.",
                details={"valid": [c.value for c in CollectionCategory]},
            ) from None
        profile.owned[cat] = set(ids)
    return profile


class CollectionTrackerService:
    """Stateless service assembling collection plans."""

    def __init__(self, client: CollectionsClient | None = None) -> None:
        self._client = client

    def plan(self, request: PlanRequest) -> dict[str, Any]:
        categories = None
        if request.categories:
            try:
                categories = [CollectionCategory(c) for c in request.categories]
            except ValueError as exc:
                raise ValidationError(
                    "Unknown collection category in filter.",
                    details={"valid": [c.value for c in CollectionCategory]},
                ) from exc
        owned = _parse_owned(request.owned)
        plan = build_plan(request.spec_id, owned, categories)
        return plan.to_dict()

    async def counts(self) -> dict[str, int]:
        """Live collected counts per category (empty without Blizzard creds)."""
        if self._client is None:
            return {}
        return await self._client.counts()
