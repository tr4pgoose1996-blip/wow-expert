"""The RAG pipeline: question in, grounded and cited answer out.

Sequence:

1. Analyse the question to infer entity-type and game-version filters.
2. Retrieve candidate chunks via hybrid search.
3. Assess confidence from the retrieval evidence, *before* generation.
4. Refuse early if evidence is insufficient — no generation, no hallucination.
5. Build a token-budgeted, citation-numbered context.
6. Generate under a strict grounding prompt.
7. Verify the answer's citation markers against the context actually supplied.

Step 4 and step 7 are the two guards that make the difference between a
retrieval demo and something a player can trust.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.knowledge.confidence import (
    Citation,
    ConfidenceAssessment,
    ConfidenceLevel,
    assess_confidence,
    build_citations,
)
from app.knowledge.documents import EntityType, RetrievedChunk
from app.knowledge.embeddings import EmbeddingProvider
from app.knowledge.vector_store import SearchFilters, VectorStore
from app.services.ai_provider import AIProvider, ChatMessage

logger = get_logger(__name__)

__all__ = ["Answer", "RagPipeline"]


SYSTEM_PROMPT = """\
You are wow! expert., a World of Warcraft Game Master and coach.

You answer ONLY from the numbered CONTEXT passages provided. This is absolute.

Rules:
1. Every factual claim must be followed by its citation marker, e.g. [1] or [2][3].
2. If the context does not contain the answer, say so plainly. Never fill a gap
   with general knowledge, and never guess at numbers, item names or mechanics.
3. If sources disagree, say so and cite both. Prefer Blizzard API data, which is
   first-party and authoritative, over community sources.
4. WoW changes every patch. If a claim looks version-sensitive and the context
   does not confirm the current patch, say so.
5. Never invent a citation number that is not in the context.
6. Be direct and practical. A player wants to know what to do next.
7. Write in clear prose. Use a short list only when the answer is genuinely a
   sequence of steps or a set of items.
"""

INSUFFICIENT_TEMPLATE = (
    "I don't have enough indexed material to answer that reliably.\n\n"
    "{detail}\n\n"
    "Rather than guess at something that could waste your time in game, "
    "here is what would help: try naming the specific quest, item, boss or "
    "zone, and say whether you mean retail or Classic."
)

#: Matches the citation markers the model is instructed to emit.
_CITATION_MARKER = re.compile(r"\[(\d{1,2})\]")

#: Keyword hints mapping question language to entity types. Used to bias
#: retrieval, never to hard-filter — a wrong guess must not starve results.
_ENTITY_HINTS: dict[EntityType, tuple[str, ...]] = {
    EntityType.QUEST: ("quest", "questline", "objective", "turn in", "breadcrumb"),
    EntityType.NPC: ("npc", "vendor", "trainer", "quest giver", "who is"),
    EntityType.ZONE: ("zone", "area", "region", "continent", "where is"),
    EntityType.ITEM: ("item", "gear", "weapon", "trinket", "drop", "bis", "loot"),
    EntityType.BOSS: ("boss", "encounter", "mechanic", "phase", "kill", "wipe"),
    EntityType.DUNGEON: ("dungeon", "mythic+", "mythic plus", "m+", "keystone"),
    EntityType.RAID: ("raid", "heroic", "mythic raid", "progression", "tier"),
    EntityType.PROFESSION: (
        "profession", "crafting", "recipe", "herbalism", "mining",
        "enchanting", "alchemy", "blacksmithing", "leatherworking",
        "tailoring", "jewelcrafting", "inscription", "engineering", "skinning",
    ),
    EntityType.ACHIEVEMENT: ("achievement", "meta", "feat of strength"),
    EntityType.MOUNT: ("mount", "flying", "ground mount", "drake"),
    EntityType.PET: ("pet", "battle pet", "companion", "hunter pet", "tame"),
    EntityType.REPUTATION: ("reputation", "rep", "faction", "exalted", "renown"),
    EntityType.CLASS_SPEC: (
        "talent", "spec", "rotation", "build", "stat priority", "how do i play",
    ),
}

_CLASSIC_HINTS = (
    "classic", "vanilla", "era", "hardcore", "season of discovery", "sod",
    "wrath", "tbc", "burning crusade", "cataclysm classic", "mists classic",
)


@dataclass(slots=True)
class Answer:
    """A generated answer with its full provenance."""

    question: str
    answer: str
    confidence: ConfidenceAssessment
    citations: list[Citation] = field(default_factory=list)
    #: True when the pipeline declined to answer for lack of evidence.
    refused: bool = False
    retrieved_count: int = 0
    latency_ms: int = 0
    model: str = ""
    filters_applied: dict[str, Any] = field(default_factory=dict)
    #: Populated when generated citation markers failed verification.
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "refused": self.refused,
            "confidence": self.confidence.as_dict(),
            "citations": [
                {
                    "index": c.index,
                    "title": c.title,
                    "url": c.url,
                    "source": c.source.value,
                    "source_name": c.source_name,
                    "attribution": c.attribution,
                    "entity_type": c.entity_type,
                    "location": c.location,
                    "relevance": c.relevance,
                    "excerpt": c.excerpt,
                }
                for c in self.citations
            ],
            "retrieved_count": self.retrieved_count,
            "latency_ms": self.latency_ms,
            "model": self.model,
            "filters_applied": self.filters_applied,
            "warnings": self.warnings,
        }


class RagPipeline:
    """Orchestrates retrieval-augmented answering."""

    #: Characters of context given to the model. ~6k tokens, leaving ample
    #: room for the system prompt and a full answer on a 16k-context model.
    _CONTEXT_CHAR_BUDGET = 24_000
    _DEFAULT_TOP_K = 8

    def __init__(
        self,
        store: VectorStore,
        embedder: EmbeddingProvider,
        ai_provider: AIProvider,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._ai = ai_provider

    # -- Query analysis --------------------------------------------------

    @staticmethod
    def infer_filters(question: str) -> SearchFilters:
        """Infer retrieval filters from the question's wording.

        Only the game-version filter is applied as a hard constraint, because
        serving a Classic answer to a retail player (or vice versa) is a
        confident, plausible, and completely wrong answer — the worst kind.
        Entity hints are deliberately *not* hard filters: a misclassified
        question should degrade ranking, not eliminate the right document.
        """
        lowered = question.lower()
        game_version = (
            "classic" if any(h in lowered for h in _CLASSIC_HINTS) else None
        )
        return SearchFilters(game_version=game_version)

    @staticmethod
    def _entity_hints(question: str) -> list[EntityType]:
        """Entity types the question appears to be about."""
        lowered = question.lower()
        return [
            entity
            for entity, keywords in _ENTITY_HINTS.items()
            if any(keyword in lowered for keyword in keywords)
        ]

    @staticmethod
    def _apply_hint_boost(
        retrieved: list[RetrievedChunk], hints: list[EntityType]
    ) -> list[RetrievedChunk]:
        """Nudge chunks matching the inferred entity type up the ranking.

        A small multiplicative boost — enough to break ties between otherwise
        comparable chunks, too small to promote a genuinely poor match.
        """
        if not hints:
            return retrieved
        hint_set = set(hints)
        for item in retrieved:
            if item.chunk.entity_type in hint_set:
                item.score *= 1.15
        reranked = sorted(retrieved, key=lambda r: r.score, reverse=True)
        for position, item in enumerate(reranked, start=1):
            item.rank = position
        return reranked

    # -- Context assembly ------------------------------------------------

    def _build_context(
        self, retrieved: list[RetrievedChunk], citations: list[Citation]
    ) -> tuple[str, set[int]]:
        """Render numbered context passages within the token budget.

        Returns the context string and the set of citation indices actually
        included — the verification step needs to know precisely what the
        model was shown.
        """
        by_document = {c.title: c.index for c in citations}
        index_by_key = {}
        for item in retrieved:
            index_by_key.setdefault(
                item.chunk.document_key, by_document.get(item.chunk.title)
            )

        blocks: list[str] = []
        included: set[int] = set()
        used = 0

        for item in retrieved:
            index = index_by_key.get(item.chunk.document_key)
            if index is None:
                continue
            chunk = item.chunk
            header = (
                f"[{index}] {chunk.display_location} "
                f"(source: {chunk.source.value}, {chunk.game_version})"
            )
            block = f"{header}\n{chunk.text}"
            if used + len(block) > self._CONTEXT_CHAR_BUDGET and blocks:
                break
            blocks.append(block)
            included.add(index)
            used += len(block)

        return "\n\n---\n\n".join(blocks), included

    @staticmethod
    def _verify_citations(
        answer_text: str, valid_indices: set[int]
    ) -> tuple[str, list[str]]:
        """Strip citation markers the model invented.

        A fabricated citation is worse than none: it looks like provenance
        while pointing nowhere. Any marker not present in the supplied context
        is removed and reported as a warning.
        """
        warnings: list[str] = []
        invented: set[int] = {
            int(m.group(1))
            for m in _CITATION_MARKER.finditer(answer_text)
            if int(m.group(1)) not in valid_indices
        }
        if invented:
            warnings.append(
                "Removed unsupported citation marker(s): "
                + ", ".join(f"[{i}]" for i in sorted(invented))
            )
            for index in invented:
                answer_text = answer_text.replace(f"[{index}]", "")
            answer_text = re.sub(r"\s{2,}", " ", answer_text)
            answer_text = re.sub(r"\s+([.,;:])", r"\1", answer_text)
        return answer_text.strip(), warnings

    # -- Entry point -----------------------------------------------------

    async def answer(
        self,
        question: str,
        *,
        top_k: int | None = None,
        filters: SearchFilters | None = None,
        character_context: str | None = None,
    ) -> Answer:
        """Answer ``question`` from indexed knowledge.

        Args:
            question: The player's question.
            top_k: Chunks to retrieve. Defaults to 8.
            filters: Explicit filters, overriding inference.
            character_context: Optional description of the asking player's
                character, so advice can be personalised. Never treated as
                a factual source.

        Returns:
            An :class:`Answer` carrying the text, citations and confidence.
            Check ``refused`` before presenting it as authoritative.
        """
        started = time.perf_counter()
        question = question.strip()
        if not question:
            raise ValueError("question must not be empty")

        active_filters = filters or self.infer_filters(question)
        limit = top_k or self._DEFAULT_TOP_K

        query_embedding = await self._embedder.embed_one(question)
        retrieved = await self._store.hybrid_search(
            question,
            query_embedding,
            limit=limit,
            filters=active_filters,
        )
        retrieved = self._apply_hint_boost(retrieved, self._entity_hints(question))

        confidence = assess_confidence(retrieved, question=question)
        citations = build_citations(retrieved)

        def elapsed_ms() -> int:
            return int((time.perf_counter() - started) * 1000)

        # Guard: refuse rather than generate from insufficient evidence.
        if not confidence.level.should_answer:
            logger.info(
                "Refusing to answer (insufficient evidence): %r", question[:120]
            )
            detail = " ".join(confidence.reasons) or "Nothing relevant was found."
            return Answer(
                question=question,
                answer=INSUFFICIENT_TEMPLATE.format(detail=detail),
                confidence=confidence,
                citations=citations,
                refused=True,
                retrieved_count=len(retrieved),
                latency_ms=elapsed_ms(),
                filters_applied=active_filters.describe(),
            )

        context, included = self._build_context(retrieved, citations)
        # Citations the model never saw would be dead links in the response.
        citations = [c for c in citations if c.index in included]

        user_parts = [f"QUESTION:\n{question}"]
        if character_context:
            user_parts.append(
                "ASKING PLAYER'S CHARACTER (context for tailoring advice; "
                f"not a factual source):\n{character_context}"
            )
        user_parts.append(f"CONTEXT:\n{context}")
        user_parts.append(
            "Answer the question using only the context above. "
            "Cite every claim with its bracketed number."
        )

        completion = await self._ai.complete(
            [
                ChatMessage(role="system", content=SYSTEM_PROMPT),
                ChatMessage(role="user", content="\n\n".join(user_parts)),
            ],
            temperature=0.2,  # low: this is a factual task, not a creative one
            max_tokens=900,
        )

        answer_text, warnings = self._verify_citations(completion.content, included)

        # Downgrade confidence if the model cited nothing — an uncited answer
        # is not demonstrably grounded, whatever retrieval suggested.
        if not _CITATION_MARKER.search(answer_text) and citations:
            warnings.append(
                "The generated answer contained no citations; confidence "
                "was downgraded."
            )
            if confidence.level is ConfidenceLevel.HIGH:
                confidence.level = ConfidenceLevel.MEDIUM
            elif confidence.level is ConfidenceLevel.MEDIUM:
                confidence.level = ConfidenceLevel.LOW

        return Answer(
            question=question,
            answer=answer_text,
            confidence=confidence,
            citations=citations,
            refused=False,
            retrieved_count=len(retrieved),
            latency_ms=elapsed_ms(),
            model=completion.model,
            filters_applied=active_filters.describe(),
            warnings=warnings,
        )
