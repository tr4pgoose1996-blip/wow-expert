"""The Game Master reasoning engine.

Combines every subsystem into one coherent plan: it infers intent from the
player's words + profile, decomposes that into ordered objectives sourced from
the rotation, gear, coach, and collections modules, ranks them with an
efficiency model (impact per minute, gated by what the player can do now), and
attaches a *why* to each step. The engine is pure logic over injected advisors
so it is fully unit-testable without a database or an LLM. An LLM can later
enrich the prose, but the plan structure and ordering never depend on it.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Protocol

from app.modules.reasoning.domain import (
    IntentKind,
    Objective,
    Plan,
    PlayerContext,
)


class SubsystemAdvisor(Protocol):
    """A pluggable source of objectives for the plan."""

    source_name: str

    def contribute(
        self, intent: IntentKind, context: PlayerContext, intent_text: str
    ) -> list[Objective]: ...


# --- Intent inference ------------------------------------------------------

# Each group carries a weight: gear/rotation are stronger signals than generic
# content words, so "upgrade for Mythic+" is read as a gear intent, not a
# "clear content" intent. Tokens are matched on word boundaries to avoid
# accidental substring hits.
_GEAR_TOKENS = {
    "gear": 3,
    "upgrade": 3,
    "item level": 3,
    "ilevel": 3,
    "ilvl": 3,
    "trinket": 2,
    "enchant": 2,
    "craft": 2,
}
_LEARN_TOKENS = {"learn": 2, "how to": 2, "improve": 1, "practice": 2, "better at": 1}
_CONTENT_TOKENS = {
    "dungeon": 1,
    "raid": 1,
    "boss": 1,
    "mythic": 1,
    "m+": 1,
    "mythic+": 1,
    "clear": 1,
    "progress": 1,
    "push": 1,
}
_COLLECT_TOKENS = {
    "collect": 2,
    "mount": 2,
    "pet": 2,
    "toy": 2,
    "achievement": 2,
    "transmog": 2,
    "title": 1,
    "tabard": 1,
    "farm": 1,
}
_ROTATION_TOKENS = {"rotation": 3, "what to press": 3, "priority": 2, "spell": 1}


def _token_hits(text: str, token: str) -> int:
    """Count whole-word/phrase occurrences so 'gear' doesn't match 'gearbox'."""
    if " " in token:
        return text.count(token)
    return len(re.findall(rf"\b{re.escape(token)}\b", text))


def infer_intent(text: str, context: PlayerContext) -> IntentKind:
    t = text.lower()
    scores: dict[IntentKind, int] = dict.fromkeys(IntentKind, 0)
    scores[IntentKind.GEAR_UP] += sum(
        _token_hits(t, tok) * w for tok, w in _GEAR_TOKENS.items()
    )
    scores[IntentKind.LEARN_SPEC] += sum(
        _token_hits(t, tok) * w for tok, w in _LEARN_TOKENS.items()
    )
    scores[IntentKind.CLEAR_CONTENT] += sum(
        _token_hits(t, tok) * w for tok, w in _CONTENT_TOKENS.items()
    )
    scores[IntentKind.COMPLETE_COLLECTION] += sum(
        _token_hits(t, tok) * w for tok, w in _COLLECT_TOKENS.items()
    )
    scores[IntentKind.IMPROVE_ROTATION] += sum(
        _token_hits(t, tok) * w for tok, w in _ROTATION_TOKENS.items()
    )
    # Profile signals break ties / reinforce broad intents.
    if context.favorite_content:
        scores[IntentKind.CLEAR_CONTENT] += 1
    if context.current_goals:
        scores[IntentKind.PROGRESS_CHARACTER] += 1
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return IntentKind.OPEN
    return best


# --- Efficiency model ------------------------------------------------------


def _rank(objectives: list[Objective]) -> list[Objective]:
    """Order by priority desc, then by ETA asc so quick wins surface first."""
    return sorted(objectives, key=lambda o: (-o.priority, o.eta_minutes))


def _combine(objectives: list[Objective], cap: int = 8) -> list[Objective]:
    """Keep the highest-priority objectives and renumber ids in plan order."""
    ranked = _rank(objectives)[:cap]
    for i, o in enumerate(ranked, 1):
        ranked[i - 1] = replace(o, id=f"obj_{i}")
    return ranked


class GameMasterEngine:
    """Reason over intent + profile + subsystems to produce an explained plan."""

    def __init__(self, advisors: list[SubsystemAdvisor] | None = None) -> None:
        self._advisors = advisors or _default_advisors()

    def plan(self, intent_text: str, context: PlayerContext) -> Plan:
        intent = infer_intent(intent_text, context)
        objectives: list[Objective] = []
        for adv in self._advisors:
            try:
                objectives.extend(adv.contribute(intent, context, intent_text))
            except Exception:
                continue

        ranked = _combine(objectives)
        confidence = self._confidence(intent, context, ranked)
        summary = self._summarize(intent, ranked, context)
        open_q = self._open_questions(intent, context, ranked)

        return Plan(
            intent=intent,
            intent_text=intent_text,
            objectives=ranked,
            summary=summary,
            confidence=confidence,
            adaptations=self._adaptations(context),
            open_questions=open_q,
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _confidence(
        intent: IntentKind, context: PlayerContext, ranked: list[Objective]
    ) -> float:
        if not ranked:
            return 0.3
        if intent == IntentKind.OPEN and not context.spec_id:
            return 0.4
        base = 0.7 + 0.1 * min(len(ranked), 3)
        # Higher when we know the player's spec and content focus.
        if context.spec_id:
            base += 0.05
        if context.favorite_content:
            base += 0.05
        return min(round(base, 2), 0.98)

    @staticmethod
    def _summarize(
        intent: IntentKind, ranked: list[Objective], context: PlayerContext
    ) -> str:
        if not ranked:
            return (
                "I don't have enough to plan yet. Tell me your spec, goals, or the "
                "content you want to tackle and I'll lay out the fastest path."
            )
        top = ranked[0]
        who = f"your {context.spec or 'character'}" if context.spec else "you"
        return (
            f"For {who}, the highest-value step right now is: {top.title}. "
            f"Reason: {top.rationale}"
        )

    @staticmethod
    def _open_questions(
        intent: IntentKind, context: PlayerContext, ranked: list[Objective]
    ) -> list[str]:
        q: list[str] = []
        if not context.spec_id:
            q.append("Which class and specialization do you play?")
        if intent == IntentKind.CLEAR_CONTENT and not context.favorite_content:
            q.append("Which dungeon or raid are you pushing?")
        if intent == IntentKind.COMPLETE_COLLECTION and not context.current_goals:
            q.append("Which collectible or achievement is the goal?")
        if not ranked:
            q.append("What are you trying to achieve this week?")
        return q

    @staticmethod
    def _adaptations(context: PlayerContext) -> list[str]:
        out: list[str] = []
        if context.spec_id:
            out.append(
                "Plan re-ranks automatically when your item level or spec changes."
            )
        if "mythic_plus" in context.favorite_content:
            out.append(
                "In Mythic+, routes and affixes shift weekly; re-ask for a fresh plan."
            )
        return out


# --- Built-in advisors (lazy: only imported when actually planning) --------


def _default_advisors() -> list[SubsystemAdvisor]:
    from app.modules.reasoning.advisors import (
        CoachAdvisor,
        CollectionsAdvisor,
        GearAdvisorBridge,
        RotationAdvisorBridge,
    )

    return [
        GearAdvisorBridge(),
        CoachAdvisor(),
        CollectionsAdvisor(),
        RotationAdvisorBridge(),
    ]
