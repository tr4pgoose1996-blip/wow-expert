"""Vector store: persistence and hybrid retrieval over knowledge chunks.

Retrieval fuses two signals:

* **Dense** — pgvector cosine similarity, which finds paraphrases and
  conceptual matches ("how do I beat the last boss of Karazhan").
* **Lexical** — PostgreSQL full-text search, which reliably nails the exact
  proper nouns that dominate WoW questions ("Thunderfury, Blessed Blade of
  the Windseeker") where dense retrieval is notoriously weak.

The two ranked lists are combined with Reciprocal Rank Fusion. RRF is used
rather than a weighted score sum because the two scores are not on comparable
scales, and RRF depends only on rank ordering — making it robust when the
embedding model changes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.knowledge import KnowledgeChunkRow, KnowledgeDocumentRow
from app.knowledge.documents import (
    DocumentChunk,
    EntityType,
    KnowledgeDocument,
    RetrievedChunk,
)
from app.knowledge.policy import SourceKey

logger = get_logger(__name__)

__all__ = ["PgVectorStore", "SearchFilters", "VectorStore"]


class SearchFilters:
    """Optional constraints narrowing a retrieval query.

    Filtering happens inside the SQL query rather than post-hoc, so a
    restrictive filter does not silently starve the result set.
    """

    __slots__ = ("entity_types", "game_version", "sources")

    def __init__(
        self,
        *,
        sources: Sequence[SourceKey] | None = None,
        entity_types: Sequence[EntityType] | None = None,
        game_version: str | None = None,
    ) -> None:
        self.sources = list(sources) if sources else None
        self.entity_types = list(entity_types) if entity_types else None
        self.game_version = game_version

    def is_empty(self) -> bool:
        return not (self.sources or self.entity_types or self.game_version)

    def describe(self) -> dict[str, Any]:
        return {
            "sources": [s.value for s in self.sources] if self.sources else None,
            "entity_types": (
                [e.value for e in self.entity_types] if self.entity_types else None
            ),
            "game_version": self.game_version,
        }


@runtime_checkable
class VectorStore(Protocol):
    """The persistence contract the RAG pipeline depends on.

    Defined as a Protocol so a dedicated vector service could replace
    PostgreSQL without touching the pipeline.
    """

    async def upsert_document(
        self,
        document: KnowledgeDocument,
        chunks: list[DocumentChunk],
        embedding_model: str,
    ) -> bool:
        """Persist a document and its chunks. Returns True if written."""
        ...

    async def hybrid_search(
        self,
        query_text: str,
        query_embedding: list[float],
        *,
        limit: int = 10,
        filters: SearchFilters | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve the most relevant chunks for a query."""
        ...


class PgVectorStore:
    """PostgreSQL + pgvector implementation of :class:`VectorStore`."""

    #: RRF damping constant. 60 is the value from the original Cormack et al.
    #: paper and is a well-tested default; it limits how much any single
    #: retriever's top hit can dominate the fused ranking.
    _RRF_K = 60
    #: Over-fetch factor per retriever before fusion — fusion needs depth in
    #: both lists to work, otherwise it degenerates to a simple union.
    _CANDIDATE_MULTIPLIER = 4

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- Writing ---------------------------------------------------------

    async def upsert_document(
        self,
        document: KnowledgeDocument,
        chunks: list[DocumentChunk],
        embedding_model: str,
    ) -> bool:
        """Insert or update a document and replace its chunks.

        Skips all work — including the expensive embedding write — when the
        stored content hash and embedding model both already match.

        Returns:
            True if the document was written, False if it was unchanged.
        """
        existing = await self._session.scalar(
            select(KnowledgeDocumentRow).where(
                KnowledgeDocumentRow.stable_key == document.stable_key
            )
        )

        if (
            existing is not None
            and existing.content_hash == document.content_hash
            and existing.embedding_model == embedding_model
        ):
            # Still record that we saw it, so staleness sweeps stay accurate.
            existing.fetched_at = document.fetched_at
            return False

        values = {
            "stable_key": document.stable_key,
            "source": document.source.value,
            "source_id": document.source_id,
            "entity_type": document.entity_type.value,
            "title": document.title,
            "url": document.url,
            "body": document.body,
            "facts": document.facts,
            "game_version": document.game_version,
            "patch": document.patch,
            "language": document.language,
            "content_hash": document.content_hash,
            "embedding_model": embedding_model,
            "source_updated_at": document.updated_at,
            "fetched_at": document.fetched_at,
        }

        statement = (
            pg_insert(KnowledgeDocumentRow)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[KnowledgeDocumentRow.stable_key],
                set_={k: v for k, v in values.items() if k != "stable_key"},
            )
            .returning(KnowledgeDocumentRow.id)
        )
        document_id = await self._session.scalar(statement)

        # Chunk boundaries shift when content changes, so replacing wholesale
        # is both simpler and safer than diffing ordinals.
        await self._session.execute(
            delete(KnowledgeChunkRow).where(
                KnowledgeChunkRow.document_id == document_id
            )
        )

        if chunks:
            await self._session.execute(
                pg_insert(KnowledgeChunkRow),
                [
                    {
                        "document_id": document_id,
                        "ordinal": chunk.ordinal,
                        "text": chunk.text,
                        "source": chunk.source.value,
                        "entity_type": chunk.entity_type.value,
                        "title": chunk.title,
                        "url": chunk.url,
                        "game_version": chunk.game_version,
                        "heading_path": chunk.heading_path,
                        "embedding": chunk.embedding,
                    }
                    for chunk in chunks
                ],
            )
        return True

    async def delete_document(self, stable_key: str) -> bool:
        """Remove a document and its chunks. Returns True if one was deleted."""
        result = await self._session.execute(
            delete(KnowledgeDocumentRow).where(
                KnowledgeDocumentRow.stable_key == stable_key
            )
        )
        return bool(result.rowcount)

    # -- Reading ---------------------------------------------------------

    def _apply_filters(self, statement, filters: SearchFilters | None):
        """Attach filter predicates to a select statement."""
        if filters is None or filters.is_empty():
            return statement
        if filters.sources:
            statement = statement.where(
                KnowledgeChunkRow.source.in_([s.value for s in filters.sources])
            )
        if filters.entity_types:
            statement = statement.where(
                KnowledgeChunkRow.entity_type.in_(
                    [e.value for e in filters.entity_types]
                )
            )
        if filters.game_version:
            statement = statement.where(
                KnowledgeChunkRow.game_version == filters.game_version
            )
        return statement

    async def _dense_candidates(
        self,
        query_embedding: list[float],
        limit: int,
        filters: SearchFilters | None,
    ) -> list[tuple[KnowledgeChunkRow, float]]:
        """Nearest neighbours by cosine distance."""
        # pgvector's <=> is cosine DISTANCE in [0, 2]; similarity = 1 - distance.
        distance = KnowledgeChunkRow.embedding.cosine_distance(query_embedding)
        statement = (
            select(KnowledgeChunkRow, distance.label("distance"))
            .where(KnowledgeChunkRow.embedding.is_not(None))
            .order_by(distance)
            .limit(limit)
        )
        statement = self._apply_filters(statement, filters)
        rows = await self._session.execute(statement)
        return [(row[0], max(0.0, 1.0 - float(row[1]))) for row in rows]

    async def _lexical_candidates(
        self,
        query_text: str,
        limit: int,
        filters: SearchFilters | None,
    ) -> list[tuple[KnowledgeChunkRow, float]]:
        """Full-text matches ranked by ts_rank_cd.

        ``websearch_to_tsquery`` is used rather than ``plainto_tsquery``
        because it tolerates arbitrary user punctuation — apostrophes in names
        like "Mal'Ganis" otherwise raise a syntax error.
        """
        tsquery = func.websearch_to_tsquery("english", query_text)
        rank = func.ts_rank_cd(KnowledgeChunkRow.search_vector, tsquery)
        statement = (
            select(KnowledgeChunkRow, rank.label("rank"))
            .where(KnowledgeChunkRow.search_vector.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(limit)
        )
        statement = self._apply_filters(statement, filters)
        rows = await self._session.execute(statement)
        return [(row[0], float(row[1])) for row in rows]

    @staticmethod
    def _to_chunk(row: KnowledgeChunkRow) -> DocumentChunk:
        return DocumentChunk(
            document_key=str(row.document_id),
            ordinal=row.ordinal,
            text=row.text,
            source=SourceKey(row.source),
            entity_type=EntityType(row.entity_type),
            title=row.title,
            url=row.url,
            game_version=row.game_version,
            heading_path=list(row.heading_path or []),
        )

    async def hybrid_search(
        self,
        query_text: str,
        query_embedding: list[float],
        *,
        limit: int = 10,
        filters: SearchFilters | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve chunks by fused dense + lexical relevance.

        Args:
            query_text: The user's question, used for lexical matching.
            query_embedding: Its embedding, used for dense matching.
            limit: Maximum chunks to return.
            filters: Optional source/entity/version constraints.

        Returns:
            Chunks ordered by fused relevance, each carrying its component
            scores for confidence estimation and debugging.
        """
        depth = max(limit * self._CANDIDATE_MULTIPLIER, 20)

        dense = await self._dense_candidates(query_embedding, depth, filters)
        lexical = await self._lexical_candidates(query_text, depth, filters)

        # Normalise lexical scores to [0, 1] for reporting. ts_rank_cd has no
        # fixed upper bound, so scale against the best hit in this result set.
        max_lexical = max((score for _, score in lexical), default=0.0)

        fused: dict[Any, RetrievedChunk] = {}

        for rank, (row, similarity) in enumerate(dense, start=1):
            fused[row.id] = RetrievedChunk(
                chunk=self._to_chunk(row),
                vector_score=similarity,
                score=1.0 / (self._RRF_K + rank),
            )

        for rank, (row, lex_score) in enumerate(lexical, start=1):
            contribution = 1.0 / (self._RRF_K + rank)
            normalised = lex_score / max_lexical if max_lexical > 0 else 0.0
            if row.id in fused:
                fused[row.id].score += contribution
                fused[row.id].lexical_score = normalised
            else:
                fused[row.id] = RetrievedChunk(
                    chunk=self._to_chunk(row),
                    lexical_score=normalised,
                    score=contribution,
                )

        ranked = sorted(fused.values(), key=lambda r: r.score, reverse=True)[:limit]
        for position, retrieved in enumerate(ranked, start=1):
            retrieved.rank = position

        logger.debug(
            "hybrid_search: %d dense, %d lexical, %d fused, returning %d",
            len(dense), len(lexical), len(fused), len(ranked),
        )
        return ranked

    async def count_documents(self, source: SourceKey | None = None) -> int:
        """Number of indexed documents, optionally for one source."""
        statement = select(func.count()).select_from(KnowledgeDocumentRow)
        if source is not None:
            statement = statement.where(
                KnowledgeDocumentRow.source == source.value
            )
        return int(await self._session.scalar(statement) or 0)

    async def stats(self) -> dict[str, Any]:
        """Per-source index statistics for the health/admin endpoints."""
        rows = await self._session.execute(
            select(
                KnowledgeDocumentRow.source,
                KnowledgeDocumentRow.entity_type,
                func.count().label("documents"),
                func.max(KnowledgeDocumentRow.fetched_at).label("last_fetched"),
            ).group_by(
                KnowledgeDocumentRow.source, KnowledgeDocumentRow.entity_type
            )
        )
        by_source: dict[str, Any] = {}
        for source, entity_type, count, last_fetched in rows:
            entry = by_source.setdefault(
                source, {"documents": 0, "entities": {}, "last_fetched": None}
            )
            entry["documents"] += count
            entry["entities"][entity_type] = count
            if last_fetched is not None and (
                entry["last_fetched"] is None
                or last_fetched > entry["last_fetched"]
            ):
                entry["last_fetched"] = last_fetched

        total_chunks = await self._session.scalar(
            select(func.count()).select_from(KnowledgeChunkRow)
        )
        embedded_chunks = await self._session.scalar(
            select(func.count())
            .select_from(KnowledgeChunkRow)
            .where(KnowledgeChunkRow.embedding.is_not(None))
        )
        return {
            "sources": by_source,
            "total_chunks": int(total_chunks or 0),
            "embedded_chunks": int(embedded_chunks or 0),
        }
