"""Ingestion pipeline: connector output to searchable index.

Responsibilities:

* Stream documents from a connector without buffering an entire source.
* Skip unchanged documents by content hash, so a re-run is cheap.
* Batch embedding calls, which are the dominant cost.
* Commit incrementally, so a failure mid-run does not discard hours of work.
* Record an auditable :class:`IngestionRun` for every attempt.

Failures on individual documents are logged and counted rather than raised: a
single malformed wiki page must not abort a multi-hour crawl.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.knowledge import IngestionRun
from app.knowledge.chunking import ChunkingConfig, chunk_document
from app.knowledge.documents import DocumentChunk, KnowledgeDocument
from app.knowledge.embeddings import EmbeddingProvider
from app.knowledge.policy import SourceKey, UsageTier, get_policy
from app.knowledge.vector_store import PgVectorStore

logger = get_logger(__name__)

__all__ = ["IngestionPipeline", "IngestionResult"]


@dataclass(slots=True)
class IngestionResult:
    """Outcome of one ingestion run."""

    source: SourceKey
    tier: UsageTier
    documents_seen: int = 0
    documents_written: int = 0
    documents_skipped: int = 0
    chunks_written: int = 0
    embeddings_created: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def status(self) -> str:
        if self.errors and not self.documents_written:
            return "failed"
        if self.errors:
            return "partial"
        return "completed"

    def summary(self) -> str:
        return (
            f"{self.source.value}: {self.documents_written} written, "
            f"{self.documents_skipped} unchanged, "
            f"{self.chunks_written} chunks, "
            f"{self.embeddings_created} embeddings, "
            f"{len(self.errors)} errors in {self.duration_seconds:.1f}s"
        )


class IngestionPipeline:
    """Drives documents from a connector into the vector store."""

    #: Documents held before a flush. Bounds memory and caps the work lost to
    #: a crash, while keeping embedding batches usefully large.
    _FLUSH_EVERY = 32
    #: Chunks per embedding API call.
    _EMBED_BATCH = 64

    def __init__(
        self,
        session: AsyncSession,
        embedder: EmbeddingProvider,
        *,
        chunking: ChunkingConfig | None = None,
    ) -> None:
        self._session = session
        self._embedder = embedder
        self._store = PgVectorStore(session)
        self._chunking = chunking or ChunkingConfig()

    async def ingest(
        self,
        source: SourceKey,
        documents: AsyncIterator[KnowledgeDocument],
        *,
        dry_run: bool = False,
    ) -> IngestionResult:
        """Consume ``documents`` and index them.

        Args:
            source: The source being ingested, for policy and auditing.
            documents: Async stream from a connector.
            dry_run: Parse and chunk without writing or embedding. Useful for
                validating a new connector cheaply.

        Returns:
            The run result. Never raises for per-document failures.
        """
        policy = get_policy(source)
        result = IngestionResult(source=source, tier=policy.tier)
        started = time.perf_counter()

        run = IngestionRun(
            source=source.value,
            usage_tier=policy.tier.value,
            status="running",
            started_at=datetime.now(UTC),
        )
        if not dry_run:
            self._session.add(run)
            await self._session.flush()

        logger.info(
            "Ingestion started: %s (tier=%s, dry_run=%s)",
            policy.display_name, policy.tier.value, dry_run,
        )

        pending: list[tuple[KnowledgeDocument, list[DocumentChunk]]] = []

        try:
            async for document in documents:
                result.documents_seen += 1
                try:
                    chunks = chunk_document(document, self._chunking)
                except Exception as exc:
                    message = f"chunking {document.stable_key}: {exc}"
                    logger.warning("Ingestion error: %s", message)
                    result.errors.append(message)
                    continue

                pending.append((document, chunks))
                if len(pending) >= self._FLUSH_EVERY:
                    await self._flush(pending, result, dry_run)
                    pending = []

            if pending:
                await self._flush(pending, result, dry_run)

        except Exception as exc:
            logger.exception("Ingestion aborted for %s", source.value)
            result.errors.append(f"fatal: {exc}")
            if not dry_run:
                run.status = "failed"
                run.error = str(exc)[:2000]
                run.finished_at = datetime.now(UTC)
                await self._session.commit()
            result.duration_seconds = time.perf_counter() - started
            return result

        result.duration_seconds = time.perf_counter() - started

        if not dry_run:
            run.status = result.status
            run.documents_seen = result.documents_seen
            run.documents_written = result.documents_written
            run.documents_skipped = result.documents_skipped
            run.chunks_written = result.chunks_written
            run.embeddings_created = result.embeddings_created
            run.finished_at = datetime.now(UTC)
            run.duration_seconds = result.duration_seconds
            run.details = {
                "errors": result.errors[:50],
                "error_count": len(result.errors),
                "embedding_model": self._embedder.model_name,
            }
            if result.errors:
                run.error = result.errors[0][:2000]
            await self._session.commit()

        logger.info("Ingestion finished: %s", result.summary())
        return result

    async def _flush(
        self,
        batch: list[tuple[KnowledgeDocument, list[DocumentChunk]]],
        result: IngestionResult,
        dry_run: bool,
    ) -> None:
        """Embed and persist one batch of documents."""
        if dry_run:
            result.documents_written += len(batch)
            result.chunks_written += sum(len(chunks) for _, chunks in batch)
            return

        model = self._embedder.model_name

        # Determine which documents actually changed before spending money on
        # embeddings — this is what makes incremental re-runs cheap.
        changed: list[tuple[KnowledgeDocument, list[DocumentChunk]]] = []
        for document, chunks in batch:
            if await self._is_unchanged(document, model):
                result.documents_skipped += 1
            else:
                changed.append((document, chunks))

        if not changed:
            await self._session.commit()
            return

        all_chunks = [chunk for _, chunks in changed for chunk in chunks]
        try:
            for start in range(0, len(all_chunks), self._EMBED_BATCH):
                window = all_chunks[start : start + self._EMBED_BATCH]
                vectors = await self._embedder.embed([c.text for c in window])
                for chunk, vector in zip(window, vectors, strict=True):
                    chunk.embedding = vector
                result.embeddings_created += len(window)
        except Exception as exc:
            message = f"embedding batch failed: {exc}"
            logger.error("Ingestion error: %s", message)
            result.errors.append(message)
            await self._session.rollback()
            return

        for document, chunks in changed:
            try:
                written = await self._store.upsert_document(
                    document, chunks, model
                )
                if written:
                    result.documents_written += 1
                    result.chunks_written += len(chunks)
                else:
                    result.documents_skipped += 1
            except Exception as exc:
                message = f"writing {document.stable_key}: {exc}"
                logger.warning("Ingestion error: %s", message)
                result.errors.append(message)

        await self._session.commit()

    async def _is_unchanged(
        self, document: KnowledgeDocument, model: str
    ) -> bool:
        """Whether the stored copy already matches this document exactly."""
        from sqlalchemy import select

        from app.db.models.knowledge import KnowledgeDocumentRow

        row = await self._session.execute(
            select(
                KnowledgeDocumentRow.content_hash,
                KnowledgeDocumentRow.embedding_model,
            ).where(KnowledgeDocumentRow.stable_key == document.stable_key)
        )
        pair = row.first()
        if pair is None:
            return False
        content_hash, embedding_model = pair
        return content_hash == document.content_hash and embedding_model == model
