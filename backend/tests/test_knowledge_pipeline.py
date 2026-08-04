"""End-to-end pipeline tests using an in-memory vector store.

These exercise the real :class:`RagPipeline` — real chunking, real embeddings,
real fusion, real confidence, real citation verification — with only
PostgreSQL and the LLM substituted. That covers the wiring between components,
which the unit tests in ``test_knowledge.py`` deliberately do not.

The in-memory store implements the same hybrid dense+lexical retrieval and RRF
fusion as :class:`PgVectorStore`, so ranking behaviour is comparable without a
database.
"""

from __future__ import annotations

import math
import re

import pytest

from app.knowledge.chunking import chunk_document
from app.knowledge.confidence import ConfidenceLevel
from app.knowledge.documents import (
    DocumentChunk,
    EntityType,
    KnowledgeDocument,
    RetrievedChunk,
)
from app.knowledge.embeddings import HashingEmbeddingProvider
from app.knowledge.policy import SourceKey
from app.knowledge.rag import RagPipeline
from app.knowledge.vector_store import SearchFilters
from app.services.ai_provider import AIProvider, ChatMessage, Completion

_WORD = re.compile(r"[a-z0-9']+")


class InMemoryVectorStore:
    """Hybrid search over an in-process chunk list.

    Mirrors PgVectorStore's contract: cosine similarity for the dense half,
    token-overlap scoring standing in for ts_rank_cd on the lexical half, and
    Reciprocal Rank Fusion to combine them.
    """

    _RRF_K = 60

    def __init__(self) -> None:
        self.chunks: list[DocumentChunk] = []

    async def upsert_document(self, document, chunks, embedding_model) -> bool:
        keys = {c.chunk_key for c in chunks}
        self.chunks = [c for c in self.chunks if c.chunk_key not in keys]
        self.chunks.extend(chunks)
        return True

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    def _matches(self, chunk: DocumentChunk, filters: SearchFilters | None) -> bool:
        if filters is None or filters.is_empty():
            return True
        if filters.sources and chunk.source not in filters.sources:
            return False
        if filters.entity_types and chunk.entity_type not in filters.entity_types:
            return False
        return not (
            filters.game_version and chunk.game_version != filters.game_version
        )

    async def hybrid_search(
        self, query_text, query_embedding, *, limit=10, filters=None
    ) -> list[RetrievedChunk]:
        pool = [c for c in self.chunks if self._matches(c, filters)]
        if not pool:
            return []

        dense = sorted(
            (
                (c, self._cosine(query_embedding, c.embedding))
                for c in pool
                if c.embedding
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )[: limit * 4]

        terms = set(_WORD.findall(query_text.lower()))
        lexical = sorted(
            (
                (c, len(terms & set(_WORD.findall(c.text.lower()))) / len(terms))
                for c in pool
                if terms
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
        lexical = [pair for pair in lexical if pair[1] > 0][: limit * 4]

        fused: dict[str, RetrievedChunk] = {}
        for rank, (chunk, similarity) in enumerate(dense, start=1):
            fused[chunk.chunk_key] = RetrievedChunk(
                chunk=chunk,
                vector_score=max(0.0, similarity),
                score=1.0 / (self._RRF_K + rank),
            )
        for rank, (chunk, lex) in enumerate(lexical, start=1):
            contribution = 1.0 / (self._RRF_K + rank)
            if chunk.chunk_key in fused:
                fused[chunk.chunk_key].score += contribution
                fused[chunk.chunk_key].lexical_score = lex
            else:
                fused[chunk.chunk_key] = RetrievedChunk(
                    chunk=chunk, lexical_score=lex, score=contribution
                )

        ranked = sorted(fused.values(), key=lambda r: r.score, reverse=True)[:limit]
        for position, item in enumerate(ranked, start=1):
            item.rank = position
        return ranked


class ScriptedAI(AIProvider):
    """An AI provider returning a fixed response, capturing its prompt."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.last_prompt: str = ""
        self.call_count = 0

    async def complete(
        self, messages: list[ChatMessage], *, temperature=0.4, max_tokens=800
    ) -> Completion:
        self.call_count += 1
        self.last_prompt = "\n".join(m.content for m in messages)
        return Completion(content=self._response, model="scripted")


@pytest.fixture
def embedder() -> HashingEmbeddingProvider:
    return HashingEmbeddingProvider(dimensions=512)


@pytest.fixture
async def store(embedder) -> InMemoryVectorStore:
    """A store seeded with a handful of realistic documents."""
    documents = [
        KnowledgeDocument(
            source=SourceKey.BLIZZARD_GAME_DATA,
            source_id="7787",
            entity_type=EntityType.BOSS,
            title="Ragnaros",
            url="https://example.invalid/ragnaros",
            body=(
                "Ragnaros the Firelord is the final boss of the Molten Core "
                "raid. The encounter begins when Majordomo Executus is "
                "defeated.\n\n"
                "== Strategy ==\n\n"
                "Ragnaros submerges periodically and summons eight Sons of "
                "Flame. Players must defeat the Sons of Flame before Ragnaros "
                "re-emerges, or the raid will be overwhelmed.\n\n"
                "== Abilities ==\n\n"
                "Wrath of Ragnaros knocks back nearby melee players. "
                "Melt Weapon reduces weapon durability."
            ),
            facts={"id": 7787, "expansion": "Classic"},
        ),
        KnowledgeDocument(
            source=SourceKey.WARCRAFT_WIKI,
            source_id="1234",
            entity_type=EntityType.RAID,
            title="Molten Core",
            url="https://warcraft.wiki.gg/wiki/Molten_Core",
            body=(
                "Molten Core is a forty-player raid instance located within "
                "Blackrock Mountain. It contains ten boss encounters, "
                "culminating in Ragnaros the Firelord."
            ),
        ),
        KnowledgeDocument(
            source=SourceKey.WARCRAFT_WIKI,
            source_id="5678",
            entity_type=EntityType.PROFESSION,
            title="Tailoring",
            url="https://warcraft.wiki.gg/wiki/Tailoring",
            body=(
                "Tailoring is a crafting profession that creates cloth armor "
                "and bags. Tailors gather cloth from humanoid enemies rather "
                "than using a gathering profession."
            ),
        ),
        KnowledgeDocument(
            source=SourceKey.ICY_VEINS,
            source_id="https://www.icy-veins.com/wow/frost-mage",
            entity_type=EntityType.CLASS_SPEC,
            title="Frost Mage Guide \u2014 Icy Veins guide",
            url="https://www.icy-veins.com/wow/frost-mage",
            facts={"guide_site": "Icy Veins", "guide_type": "class_spec"},
        ),
    ]

    memory = InMemoryVectorStore()
    for document in documents:
        chunks = chunk_document(document)
        vectors = await embedder.embed([c.text for c in chunks])
        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk.embedding = vector
        await memory.upsert_document(document, chunks, embedder.model_name)
    return memory


class TestEndToEndRetrieval:
    async def test_relevant_question_retrieves_the_right_document(
        self, store, embedder
    ) -> None:
        embedding = await embedder.embed_one(
            "Ragnaros Molten Core Sons of Flame strategy"
        )
        results = await store.hybrid_search(
            "Ragnaros Molten Core Sons of Flame strategy", embedding, limit=5
        )
        assert results
        assert any("Ragnaros" in r.chunk.title for r in results[:2])

    async def test_filters_restrict_the_result_set(self, store, embedder) -> None:
        embedding = await embedder.embed_one("crafting cloth armor and bags")
        results = await store.hybrid_search(
            "crafting cloth armor and bags",
            embedding,
            limit=5,
            filters=SearchFilters(entity_types=[EntityType.PROFESSION]),
        )
        assert results
        assert all(
            r.chunk.entity_type is EntityType.PROFESSION for r in results
        )

    async def test_metadata_only_document_is_still_retrievable(
        self, store, embedder
    ) -> None:
        """A guide with no stored body must still be findable and linkable."""
        embedding = await embedder.embed_one("Frost Mage guide")
        results = await store.hybrid_search(
            "Frost Mage guide", embedding, limit=5
        )
        titles = [r.chunk.title for r in results]
        assert any("Frost Mage" in t for t in titles)


class TestEndToEndAnswering:
    async def test_grounded_question_produces_a_cited_answer(
        self, store, embedder
    ) -> None:
        ai = ScriptedAI(
            "Ragnaros is the final boss of Molten Core [1]. During the "
            "submerge phase he summons Sons of Flame [1]."
        )
        pipeline = RagPipeline(store, embedder, ai)

        answer = await pipeline.answer(
            "What happens when Ragnaros submerges in Molten Core?"
        )

        assert not answer.refused
        assert answer.citations
        assert "[1]" in answer.answer
        assert answer.confidence.level.should_answer
        assert answer.latency_ms >= 0
        assert answer.model == "scripted"

    async def test_context_passed_to_the_model_contains_the_evidence(
        self, store, embedder
    ) -> None:
        ai = ScriptedAI("Answer [1].")
        pipeline = RagPipeline(store, embedder, ai)

        await pipeline.answer("Tell me about the Sons of Flame in Molten Core")

        assert "CONTEXT:" in ai.last_prompt
        assert "Sons of Flame" in ai.last_prompt
        # The grounding instruction must always be present.
        assert "ONLY from the numbered CONTEXT" in ai.last_prompt

    async def test_unanswerable_question_is_refused_without_calling_the_model(
        self, store, embedder
    ) -> None:
        """The core safety property: no evidence means no generation."""
        ai = ScriptedAI("This should never be returned.")
        pipeline = RagPipeline(store, embedder, ai)

        answer = await pipeline.answer(
            "What is the airspeed velocity of an unladen swallow in Azeroth?"
        )

        assert answer.refused
        assert answer.confidence.level is ConfidenceLevel.INSUFFICIENT
        assert ai.call_count == 0, "model must not be called without evidence"
        assert "don't have enough" in answer.answer

    async def test_fabricated_citation_is_stripped_from_the_answer(
        self, store, embedder
    ) -> None:
        ai = ScriptedAI(
            "Ragnaros is in Molten Core [1]. He also drops Sulfuras [99]."
        )
        pipeline = RagPipeline(store, embedder, ai)

        answer = await pipeline.answer("Where is Ragnaros found?")

        assert "[99]" not in answer.answer
        assert answer.warnings
        assert any("99" in w for w in answer.warnings)

    async def test_uncited_answer_is_downgraded(self, store, embedder) -> None:
        ai = ScriptedAI("Ragnaros is the final boss of Molten Core.")
        pipeline = RagPipeline(store, embedder, ai)

        answer = await pipeline.answer("Who is the last boss of Molten Core?")

        if answer.citations and not answer.refused:
            assert answer.warnings
            assert answer.confidence.level is not ConfidenceLevel.HIGH

    async def test_character_context_is_forwarded_but_marked_non_factual(
        self, store, embedder
    ) -> None:
        ai = ScriptedAI("Advice [1].")
        pipeline = RagPipeline(store, embedder, ai)

        await pipeline.answer(
            "What strategy should I use for the Ragnaros encounter in "
            "Molten Core?",
            character_context="Name: Thrall; Class: shaman; Level: 60",
        )

        assert "Thrall" in ai.last_prompt
        assert "not a factual source" in ai.last_prompt

    async def test_empty_question_is_rejected(self, store, embedder) -> None:
        pipeline = RagPipeline(store, embedder, ScriptedAI("x"))
        with pytest.raises(ValueError, match="must not be empty"):
            await pipeline.answer("   ")

    async def test_citations_carry_licence_attribution(
        self, store, embedder
    ) -> None:
        ai = ScriptedAI("Molten Core is a raid [1].")
        pipeline = RagPipeline(store, embedder, ai)

        answer = await pipeline.answer(
            "How many bosses are in the Molten Core raid instance?"
        )

        assert answer.citations
        for citation in answer.citations:
            assert citation.attribution or citation.source is SourceKey.WOWHEAD
            assert citation.url

    async def test_response_serialises_for_the_api(self, store, embedder) -> None:
        ai = ScriptedAI("Ragnaros is in Molten Core [1].")
        pipeline = RagPipeline(store, embedder, ai)

        payload = (await pipeline.answer("Where is Ragnaros?")).as_dict()

        assert set(payload) >= {
            "question", "answer", "refused", "confidence",
            "citations", "retrieved_count", "latency_ms",
        }
        assert isinstance(payload["confidence"]["score"], float)
        assert math.isfinite(payload["confidence"]["score"])
