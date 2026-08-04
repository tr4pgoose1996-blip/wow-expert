"""Tests for the knowledge engine.

Coverage focuses on the behaviours that make the engine trustworthy rather
than merely functional:

* licence policy is enforced in code, not just documented;
* chunking preserves structure and never loses content;
* confidence degrades correctly and refuses when evidence is thin;
* fabricated citations are stripped from generated answers.

These run entirely offline via the deterministic hashing embedder.
"""

from __future__ import annotations

import pytest

from app.knowledge.chunking import ChunkingConfig, chunk_document
from app.knowledge.confidence import (
    ConfidenceLevel,
    assess_confidence,
    build_citations,
)
from app.knowledge.documents import (
    DocumentChunk,
    EntityType,
    KnowledgeDocument,
    RetrievedChunk,
)
from app.knowledge.embeddings import HashingEmbeddingProvider
from app.knowledge.policy import (
    SOURCE_POLICIES,
    PolicyViolationError,
    SourceKey,
    UsageTier,
    assert_text_storage_allowed,
    get_policy,
)
from app.knowledge.rag import RagPipeline

# --------------------------------------------------------------------------
# Policy enforcement
# --------------------------------------------------------------------------


class TestSourcePolicy:
    def test_every_source_has_a_reviewed_policy(self) -> None:
        for key in SourceKey:
            policy = SOURCE_POLICIES[key]
            assert policy.rationale, f"{key} has no documented rationale"
            assert policy.key is key

    def test_unregistered_source_is_refused_not_defaulted(self) -> None:
        with pytest.raises(PolicyViolationError, match="Unknown knowledge source"):
            get_policy("some_scraped_site")

    def test_wowhead_is_disabled(self) -> None:
        """robots.txt refuses AI crawlers, so ingestion must be impossible."""
        assert get_policy(SourceKey.WOWHEAD).tier is UsageTier.DISABLED

    def test_disabled_source_refuses_text_storage(self) -> None:
        with pytest.raises(PolicyViolationError, match="disabled"):
            assert_text_storage_allowed(SourceKey.WOWHEAD)

    def test_metadata_only_source_refuses_text_storage(self) -> None:
        with pytest.raises(PolicyViolationError, match="metadata_only"):
            assert_text_storage_allowed(SourceKey.ICY_VEINS)

    def test_licensed_source_allows_text_storage(self) -> None:
        policy = assert_text_storage_allowed(SourceKey.WARCRAFT_WIKI)
        assert policy.tier.allows_text_storage

    def test_share_alike_is_detected(self) -> None:
        assert get_policy(SourceKey.WARCRAFT_WIKI).requires_share_alike

    def test_no_source_permits_model_training(self) -> None:
        """Several sources signal ai-train=no; none has granted training."""
        for policy in SOURCE_POLICIES.values():
            assert not policy.allows_model_training


class TestDocumentPolicyEnforcement:
    """The type boundary must enforce policy regardless of connector code."""

    def test_disabled_source_cannot_produce_a_document(self) -> None:
        with pytest.raises(PolicyViolationError):
            KnowledgeDocument(
                source=SourceKey.WOWHEAD,
                source_id="1",
                entity_type=EntityType.ITEM,
                title="Thunderfury",
                url="https://www.wowhead.com/item=19019",
                body="Some copied text.",
            )

    def test_metadata_tier_silently_drops_body_text(self) -> None:
        document = KnowledgeDocument(
            source=SourceKey.ICY_VEINS,
            source_id="guide-1",
            entity_type=EntityType.CLASS_SPEC,
            title="Frost Mage Guide",
            url="https://www.icy-veins.com/wow/frost-mage-pve-dps-guide",
            body="The full copyrighted guide text that must not be retained.",
            facts={"spec": "Frost"},
        )
        assert document.body == ""
        # Metadata survives, so the entry is still retrievable and linkable.
        assert document.facts["spec"] == "Frost"
        assert document.title

    def test_licensed_tier_retains_body_text(self) -> None:
        document = KnowledgeDocument(
            source=SourceKey.WARCRAFT_WIKI,
            source_id="42",
            entity_type=EntityType.BOSS,
            title="Ragnaros",
            url="https://warcraft.wiki.gg/wiki/Ragnaros",
            body="Ragnaros the Firelord is the final boss of Molten Core.",
        )
        assert "Firelord" in document.body

    def test_attribution_is_carried_on_every_document(self) -> None:
        document = KnowledgeDocument(
            source=SourceKey.WARCRAFT_WIKI,
            source_id="42",
            entity_type=EntityType.BOSS,
            title="Ragnaros",
            url="https://warcraft.wiki.gg/wiki/Ragnaros",
            body="Content.",
        )
        assert "CC BY-SA" in document.attribution

    def test_blank_title_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="no title"):
            KnowledgeDocument(
                source=SourceKey.WARCRAFT_WIKI,
                source_id="1",
                entity_type=EntityType.ITEM,
                title="   ",
                url="https://example.invalid",
            )

    def test_content_hash_is_stable_and_change_sensitive(self) -> None:
        def make(body: str) -> KnowledgeDocument:
            return KnowledgeDocument(
                source=SourceKey.WARCRAFT_WIKI,
                source_id="1",
                entity_type=EntityType.QUEST,
                title="A Quest",
                url="https://warcraft.wiki.gg/wiki/A_Quest",
                body=body,
            )

        assert make("same").content_hash == make("same").content_hash
        assert make("one").content_hash != make("two").content_hash


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------


class TestChunking:
    @staticmethod
    def _document(body: str) -> KnowledgeDocument:
        return KnowledgeDocument(
            source=SourceKey.WARCRAFT_WIKI,
            source_id="1",
            entity_type=EntityType.BOSS,
            title="Ragnaros",
            url="https://warcraft.wiki.gg/wiki/Ragnaros",
            body=body,
        )

    def test_short_document_yields_one_chunk(self) -> None:
        chunks = chunk_document(self._document("A short description."))
        assert len(chunks) == 1
        assert "Ragnaros" in chunks[0].text

    def test_empty_body_still_yields_a_retrievable_chunk(self) -> None:
        document = KnowledgeDocument(
            source=SourceKey.ICY_VEINS,
            source_id="g1",
            entity_type=EntityType.GUIDE,
            title="Frost Mage Guide",
            url="https://www.icy-veins.com/wow/frost-mage",
            facts={"spec": "Frost"},
        )
        chunks = chunk_document(document)
        assert len(chunks) == 1
        assert "Frost" in chunks[0].text

    def test_headings_become_heading_paths(self) -> None:
        body = (
            "Intro text about the encounter.\n\n"
            "== Strategy ==\n\n"
            "Kill the adds first.\n\n"
            "=== Phase Two ===\n\n"
            "Avoid the fire on the ground."
        )
        chunks = chunk_document(self._document(body))
        paths = [tuple(c.heading_path) for c in chunks]
        assert ("Strategy",) in paths or ("Strategy", "Phase Two") in paths

    def test_long_document_splits_with_overlap(self) -> None:
        paragraph = "This is a sentence about a raid mechanic. " * 30
        body = "\n\n".join([paragraph] * 6)
        config = ChunkingConfig(target_tokens=100, max_tokens=140, overlap_tokens=20)
        chunks = chunk_document(self._document(body), config)
        assert len(chunks) > 1
        assert all(c.text.strip() for c in chunks)

    def test_ordinals_are_sequential(self) -> None:
        body = "\n\n".join(f"Paragraph number {i}. " * 40 for i in range(8))
        chunks = chunk_document(self._document(body), ChunkingConfig(target_tokens=80))
        assert [c.ordinal for c in chunks] == list(range(len(chunks)))

    def test_oversized_paragraph_is_split_not_dropped(self) -> None:
        body = "word " * 4000
        chunks = chunk_document(
            self._document(body), ChunkingConfig(target_tokens=100, max_tokens=120)
        )
        assert len(chunks) > 1
        assert sum(c.text.count("word") for c in chunks) > 3000

    def test_every_chunk_carries_citation_metadata(self) -> None:
        chunks = chunk_document(self._document("Body text here. " * 50))
        for chunk in chunks:
            assert chunk.title == "Ragnaros"
            assert chunk.url.startswith("https://")
            assert chunk.source is SourceKey.WARCRAFT_WIKI


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------


class TestHashingEmbeddings:
    @pytest.fixture
    def embedder(self) -> HashingEmbeddingProvider:
        return HashingEmbeddingProvider(dimensions=256)

    async def test_is_deterministic(self, embedder) -> None:
        first = await embedder.embed_one("Ragnaros the Firelord")
        second = await embedder.embed_one("Ragnaros the Firelord")
        assert first == second

    async def test_vectors_are_unit_length(self, embedder) -> None:
        vector = await embedder.embed_one("Molten Core raid guide")
        magnitude = sum(v * v for v in vector) ** 0.5
        assert magnitude == pytest.approx(1.0, abs=1e-6)

    async def test_similar_text_scores_higher_than_unrelated(
        self, embedder
    ) -> None:
        def cosine(a: list[float], b: list[float]) -> float:
            return sum(x * y for x, y in zip(a, b, strict=True))

        base = await embedder.embed_one("How do I defeat Ragnaros in Molten Core")
        similar = await embedder.embed_one("Defeating Ragnaros inside Molten Core")
        unrelated = await embedder.embed_one("Tailoring recipes for cloth armor")

        assert cosine(base, similar) > cosine(base, unrelated)

    async def test_empty_text_yields_zero_vector(self, embedder) -> None:
        assert await embedder.embed_one("") == [0.0] * 256

    async def test_batch_preserves_order(self, embedder) -> None:
        texts = ["alpha", "beta", "gamma"]
        batch = await embedder.embed(texts)
        assert len(batch) == 3
        for text, vector in zip(texts, batch, strict=True):
            assert vector == await embedder.embed_one(text)


# --------------------------------------------------------------------------
# Confidence and citations
# --------------------------------------------------------------------------


def _retrieved(
    *,
    source: SourceKey = SourceKey.WARCRAFT_WIKI,
    key: str = "doc-1",
    title: str = "Ragnaros",
    score: float = 0.03,
    relevance: float = 0.8,
) -> RetrievedChunk:
    """Build a retrieved chunk.

    ``relevance`` is the absolute match quality (cosine similarity) that the
    confidence model actually reads; ``score`` is the fused RRF rank score,
    which governs ordering only.
    """
    chunk = DocumentChunk(
        document_key=key,
        ordinal=0,
        text=f"{title} is a raid boss encountered in Molten Core.",
        source=source,
        entity_type=EntityType.BOSS,
        title=title,
        url=f"https://warcraft.wiki.gg/wiki/{title}",
    )
    return RetrievedChunk(chunk=chunk, score=score, vector_score=relevance)


class TestConfidence:
    def test_no_evidence_is_insufficient(self) -> None:
        assessment = assess_confidence([])
        assert assessment.level is ConfidenceLevel.INSUFFICIENT
        assert not assessment.level.should_answer
        assert assessment.score == 0.0

    def test_weak_single_community_hit_is_insufficient(self) -> None:
        """The guard against fluent answers from unrelated chunks."""
        assessment = assess_confidence([_retrieved(relevance=0.05)])
        assert assessment.level is ConfidenceLevel.INSUFFICIENT

    def test_strong_corroborated_blizzard_evidence_is_high(self) -> None:
        retrieved = [
            _retrieved(
                source=SourceKey.BLIZZARD_GAME_DATA,
                key="blizz-1", relevance=0.92,
            ),
            _retrieved(key="wiki-1", relevance=0.88),
            _retrieved(key="wiki-2", title="Molten Core", relevance=0.85),
        ]
        assessment = assess_confidence(retrieved)
        assert assessment.level is ConfidenceLevel.HIGH
        assert assessment.level.should_answer

    def test_authority_signal_reflects_blizzard_presence(self) -> None:
        community = assess_confidence([_retrieved(relevance=0.8)])
        assert community.signals["authority"] < 1.0

        official = assess_confidence(
            [_retrieved(source=SourceKey.BLIZZARD_GAME_DATA, relevance=0.8)]
        )
        assert official.signals["authority"] == 1.0

    def test_corroboration_increases_with_distinct_documents(self) -> None:
        one = assess_confidence([_retrieved(key="a", relevance=0.8)])
        many = assess_confidence(
            [
                _retrieved(key="a", relevance=0.8),
                _retrieved(key="b", relevance=0.78),
                _retrieved(key="c", relevance=0.76),
            ]
        )
        assert many.signals["corroboration"] > one.signals["corroboration"]

    def test_every_level_has_user_facing_guidance(self) -> None:
        for level in ConfidenceLevel:
            assert level.guidance

    def test_assessment_serialises_for_the_api(self) -> None:
        payload = assess_confidence([_retrieved(relevance=0.8)]).as_dict()
        assert set(payload) == {
            "level", "score", "guidance", "reasons", "signals"
        }


class TestCitations:
    def test_chunks_from_one_document_collapse_to_one_citation(self) -> None:
        citations = build_citations(
            [
                _retrieved(key="doc-1", score=0.03),
                _retrieved(key="doc-1", score=0.02),
                _retrieved(key="doc-2", title="Molten Core", score=0.01),
            ]
        )
        assert len(citations) == 2
        assert [c.index for c in citations] == [1, 2]

    def test_licensed_source_gets_an_excerpt(self) -> None:
        citation = build_citations([_retrieved()])[0]
        assert citation.excerpt
        assert citation.attribution

    def test_metadata_only_source_is_cited_without_quoting(self) -> None:
        chunk = DocumentChunk(
            document_key="iv-1",
            ordinal=0,
            text="Frost Mage Guide — Icy Veins guide",
            source=SourceKey.ICY_VEINS,
            entity_type=EntityType.CLASS_SPEC,
            title="Frost Mage Guide",
            url="https://www.icy-veins.com/wow/frost-mage",
        )
        citation = build_citations([RetrievedChunk(chunk=chunk, score=0.03)])[0]
        assert citation.excerpt == ""
        assert citation.url  # still linkable

    def test_marker_format(self) -> None:
        assert build_citations([_retrieved()])[0].marker == "[1]"


# --------------------------------------------------------------------------
# RAG pipeline logic
# --------------------------------------------------------------------------


class TestRagPipelineLogic:
    def test_classic_questions_are_version_filtered(self) -> None:
        """Serving Classic answers to retail players is the worst failure."""
        filters = RagPipeline.infer_filters(
            "Where do I train mining in Classic Era?"
        )
        assert filters.game_version == "classic"

    def test_retail_questions_are_not_version_filtered(self) -> None:
        filters = RagPipeline.infer_filters("What is the current Mythic+ season?")
        assert filters.game_version is None

    def test_entity_hints_are_inferred_from_wording(self) -> None:
        hints = RagPipeline._entity_hints(
            "Which mount drops from this raid boss?"
        )
        assert EntityType.MOUNT in hints
        assert EntityType.RAID in hints or EntityType.BOSS in hints

    def test_hint_boost_reranks_matching_entities(self) -> None:
        boss = _retrieved(key="boss", score=0.02)
        mount_chunk = DocumentChunk(
            document_key="mount",
            ordinal=0,
            text="Ashes of Al'ar drops from Kael'thas.",
            source=SourceKey.BLIZZARD_GAME_DATA,
            entity_type=EntityType.MOUNT,
            title="Ashes of Al'ar",
            url="https://example.invalid",
        )
        mount = RetrievedChunk(chunk=mount_chunk, score=0.019)

        reranked = RagPipeline._apply_hint_boost(
            [boss, mount], [EntityType.MOUNT]
        )
        assert reranked[0].chunk.entity_type is EntityType.MOUNT
        assert reranked[0].rank == 1

    def test_fabricated_citations_are_stripped(self) -> None:
        """A citation pointing nowhere is worse than no citation."""
        text, warnings = RagPipeline._verify_citations(
            "Ragnaros is in Molten Core [1]. He drops Sulfuras [7].", {1}
        )
        assert "[7]" not in text
        assert "[1]" in text
        assert warnings and "[7]" in warnings[0]

    def test_valid_citations_survive_verification(self) -> None:
        text, warnings = RagPipeline._verify_citations(
            "Ragnaros [1] is in Molten Core [2].", {1, 2}
        )
        assert "[1]" in text and "[2]" in text
        assert not warnings
