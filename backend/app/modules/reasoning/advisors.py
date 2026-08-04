"""Subsystem advisors: bridge the reasoning engine to existing modules.

Each advisor inspects the player's intent + context and emits ``Objective``
items sourced from a real subsystem (gear, coach, collections, rotation). They
fail soft: if the player hasn't supplied a spec or the subsystem isn't relevant
to the intent, they contribute nothing rather than raising.
"""

from __future__ import annotations

from app.modules.reasoning.domain import (
    IntentKind,
    Objective,
    ObjectiveCategory,
    PlayerContext,
)
from app.modules.reasoning.engine import SubsystemAdvisor


class GearAdvisorBridge(SubsystemAdvisor):
    source_name = "gear"

    def contribute(
        self, intent: IntentKind, context: PlayerContext, intent_text: str
    ) -> list[Objective]:
        if context.spec_id is None:
            return []
        broad = {
            IntentKind.OPEN,
            IntentKind.PROGRESS_CHARACTER,
            IntentKind.CLEAR_CONTENT,
        }
        if intent not in (IntentKind.GEAR_UP, *broad):
            return []
        from app.modules.gear.domain import weights_for_spec

        weights = weights_for_spec(context.spec_id, context.role or "damage")
        top_stat = max(weights, key=weights.get)
        return [
            Objective(
                id="gear_1",
                category=ObjectiveCategory.GEAR,
                title="Optimise your highest-value secondary",
                detail=(
                    f"Prioritise {top_stat.value} on gear, gems, and enchants for "
                    f"your {context.spec or 'spec'}. It scores highest in the stat "
                    f"weights for your specialization."
                ),
                priority=90.0,
                rationale=(
                    f"{top_stat.value.title()} is your best stat; stacking it gives "
                    f"more damage or survivability per item level than any alternative."
                ),
                source=self.source_name,
                eta_minutes=30.0,
                next_action=f"Recraft or re-enchant for {top_stat.value}.",
                metadata={"top_stat": top_stat.value},
            ),
            Objective(
                id="gear_2",
                category=ObjectiveCategory.GEAR,
                title="Plan your Great Vault picks",
                detail=(
                    "The Vault is the highest item-level source per hour. Always "
                    "complete the activities that fill the most slots before claiming."
                ),
                priority=78.0,
                rationale=(
                    "Vault items are often best-in-slot and free; claiming well is "
                    "the single biggest weekly gear jump."
                ),
                source=self.source_name,
                eta_minutes=60.0,
                next_action="Run the content that fills your Vault slots, then claim.",
            ),
        ]


class CoachAdvisor(SubsystemAdvisor):
    source_name = "coach"

    def contribute(
        self, intent: IntentKind, context: PlayerContext, intent_text: str
    ) -> list[Objective]:
        if context.spec_id is None:
            return []
        wants_content = intent in (
            IntentKind.CLEAR_CONTENT,
            IntentKind.LEARN_SPEC,
            IntentKind.PROGRESS_CHARACTER,
            IntentKind.OPEN,
            IntentKind.GEAR_UP,
        )
        if not wants_content:
            return []
        role = context.role or "damage"
        diff = "mythic_plus" if "mythic_plus" in context.favorite_content else "heroic"
        return [
            Objective(
                id="coach_1",
                category=ObjectiveCategory.LEARN,
                title=f"Study the {diff} game plan for your role",
                detail=(
                    f"Pull the Combat Coach briefing for your role ({role}) at "
                    f"{diff} difficulty: interrupts, movement, cooldown timing, and "
                    f"common mistakes for the bosses you'll face."
                ),
                priority=72.0,
                rationale=(
                    "Knowing the fight beats out-gearing it. A clean kill at lower "
                    "item level beats a wipe at higher item level."
                ),
                source=self.source_name,
                eta_minutes=20.0,
                next_action="Open the coach briefing for your next boss.",
                metadata={"difficulty": diff, "role": role},
            )
        ]


class CollectionsAdvisor(SubsystemAdvisor):
    source_name = "collections"

    def contribute(
        self, intent: IntentKind, context: PlayerContext, intent_text: str
    ) -> list[Objective]:
        if intent != IntentKind.COMPLETE_COLLECTION:
            return []
        from app.modules.collections.generator import build_plan

        plan = build_plan(context.spec_id or 0)
        fastest = plan.fastest_goals[:3]
        if not fastest:
            return []
        objs: list[Objective] = []
        for i, g in enumerate(fastest, 1):
            objs.append(
                Objective(
                    id=f"collect_{i}",
                    category=ObjectiveCategory.COLLECT,
                    title=f"Grab {g.name}",
                    detail=(
                        f"{g.notes}. Obtain method: {g.obtain}. "
                        f"Roughly {g.minutes:.0f} minutes."
                    ),
                    priority=60.0 - i * 5,
                    rationale=(
                        "This is one of the fastest-obtainable collectibles left in "
                        "this category, so it's the quickest progress per effort."
                    ),
                    source=self.source_name,
                    eta_minutes=g.minutes,
                    next_action=f"Go get {g.name}.",
                    metadata={"category": g.category, "obtain": g.obtain},
                )
            )
        return objs


class RotationAdvisorBridge(SubsystemAdvisor):
    source_name = "rotation"

    def contribute(
        self, intent: IntentKind, context: PlayerContext, intent_text: str
    ) -> list[Objective]:
        if context.spec_id is None:
            return []
        if intent not in (
            IntentKind.IMPROVE_ROTATION,
            IntentKind.LEARN_SPEC,
            IntentKind.PROGRESS_CHARACTER,
            IntentKind.OPEN,
            IntentKind.CLEAR_CONTENT,
            IntentKind.GEAR_UP,
        ):
            return []
        return [
            Objective(
                id="rot_1",
                category=ObjectiveCategory.PRACTICE,
                title="Drill your single-target priority",
                detail=(
                    "Run the Rotation Advisor on your spec to see the exact priority "
                    "list and the highest-impact cooldown. Practise it on a dummy."
                ),
                priority=68.0,
                rationale=(
                    "Execution is free DPS. A tighter rotation often beats a full "
                    "item level of gear."
                ),
                source=self.source_name,
                eta_minutes=15.0,
                next_action="Open the rotation advisor for your spec.",
            )
        ]
