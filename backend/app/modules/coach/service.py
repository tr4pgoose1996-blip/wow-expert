"""Combat Coach service: maps API requests to the teaching generator.

Combines the structured seed data (always available) with optional Journal
enrichment (when Blizzard credentials + network are present). The coaching
itself is produced by :mod:`app.modules.coach.generator` and is therefore
identical in quality for every dungeon, raid, boss, and Mythic+ keystone.
"""

from __future__ import annotations

from app.core.exceptions import NotFoundError, ValidationError
from app.modules.coach.domain import (
    INSTANCE_BY_ID,
    Affix,
    Boss,
    ContentType,
    Instance,
    list_instances,
)
from app.modules.coach.generator import CoachBriefing, generate_briefing
from app.modules.coach.journal import JournalClient
from app.modules.coach.schemas import CoachBriefingRequest


class CombatCoachService:
    """Stateless service assembling coaching briefings."""

    def __init__(self, journal: JournalClient | None = None) -> None:
        self._journal = journal

    # -- Browsing -----------------------------------------------------------

    def list_instances(self, content_type: str | None = None) -> list[Instance]:
        ct = None
        if content_type is not None:
            try:
                ct = ContentType(content_type)
            except ValueError as exc:
                raise ValidationError(
                    f"Unknown content type '{content_type}'.",
                    details={"valid": [c.value for c in ContentType]},
                ) from exc
        return list_instances(ct)

    def get_instance(self, instance_id: int) -> Instance:
        inst = INSTANCE_BY_ID.get(instance_id)
        if inst is None:
            raise NotFoundError(
                f"No instance with id {instance_id}.",
                details={"known": sorted(INSTANCE_BY_ID)},
            )
        return inst

    def get_boss(self, instance_id: int, boss_id: int) -> tuple[Instance, Boss]:
        inst = self.get_instance(instance_id)
        for boss in inst.bosses:
            if boss.journal_id == boss_id:
                return inst, boss
        raise NotFoundError(
            f"No boss {boss_id} in instance {instance_id}.",
            details={"bosses": [b.journal_id for b in inst.bosses]},
        )

    # -- Coaching -----------------------------------------------------------

    async def brief(
        self,
        instance_id: int,
        boss_id: int,
        request: CoachBriefingRequest,
    ) -> CoachBriefing:
        inst, boss = self.get_boss(instance_id, boss_id)
        affixes = tuple(request.affixes)
        if request.difficulty.is_mythic_plus and not affixes:
            # A sensible default seasonal set so M+ briefings are never empty.
            affixes = (Affix.FORTIFIED,)
        briefing = generate_briefing(
            inst, boss, request.role, request.difficulty, affixes
        )
        await self._maybe_enrich(inst, boss, briefing)
        return briefing

    async def _maybe_enrich(
        self, inst: Instance, boss: Boss, briefing: CoachBriefing
    ) -> None:
        """Best-effort live enrichment; never raises on failure."""
        if self._journal is None:
            return
        try:
            data = await self._journal.get_instance(inst.journal_id)
            if data and data.get("name"):
                briefing.instance_name = data["name"]
            enc = await self._journal.get_encounter(boss.journal_id)
            if enc and enc.get("name"):
                briefing.boss_name = enc["name"]
                # If the Journal carries a description, prefer it as the summary.
                desc = enc.get("description")
                if isinstance(desc, str) and desc.strip():
                    briefing.summary = desc.strip()
        except Exception as exc:
            from app.core.logging import get_logger

            get_logger(__name__).warning("Journal enrichment skipped: %s", exc)
