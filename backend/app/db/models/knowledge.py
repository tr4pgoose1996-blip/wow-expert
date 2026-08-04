"""Knowledge engine persistence models.

Vectors live in PostgreSQL via pgvector rather than a separate vector service.
This keeps chunk text, structured metadata and embeddings in one transactional
store — so a filtered hybrid search ("retail raid bosses only") is a single
query with no cross-system consistency problem, and ingestion is atomic.

The :class:`VectorStore` protocol in ``app.knowledge.vector_store`` keeps this
choice swappable should scale later demand a dedicated index.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.session import Base, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = ["IngestionRun", "KnowledgeChunkRow", "KnowledgeDocumentRow"]


class KnowledgeDocumentRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A normalised source document.

    ``content_hash`` drives incremental ingestion: an unchanged document is
    skipped entirely, avoiding re-embedding cost on every crawl.
    """

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("stable_key", name="uq_knowledge_documents_stable_key"),
        Index("ix_knowledge_documents_source_entity", "source", "entity_type"),
        Index("ix_knowledge_documents_game_version", "game_version"),
        # Supports the staleness sweep that picks re-crawl candidates.
        Index("ix_knowledge_documents_refresh", "source", "fetched_at"),
    )

    stable_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    facts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    game_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="retail"
    )
    patch: Mapped[str | None] = mapped_column(String(32), nullable=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en_US")

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The embedding model in force when this document's chunks were written.
    #: A mismatch marks the document for re-embedding.
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    chunks: Mapped[list[KnowledgeChunkRow]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<KnowledgeDocument {self.stable_key!r} {self.title!r}>"


class KnowledgeChunkRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An embedded slice of a document, and the unit of retrieval."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "ordinal", name="uq_knowledge_chunks_doc_ordinal"
        ),
        Index("ix_knowledge_chunks_source_entity", "source", "entity_type"),
        Index("ix_knowledge_chunks_game_version", "game_version"),
        # GIN index over the generated tsvector powers the lexical half of
        # hybrid search. Created explicitly in the migration.
        Index(
            "ix_knowledge_chunks_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # Denormalised citation fields — retrieval returns chunks, and rendering a
    # citation must not require joining back to the document.
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    game_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="retail"
    )
    heading_path: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.EMBEDDING_DIMENSIONS), nullable=True
    )
    #: Maintained by a database trigger/generated column; see the migration.
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)

    document: Mapped[KnowledgeDocumentRow] = relationship(back_populates="chunks")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<KnowledgeChunk {self.title!r}#{self.ordinal}>"


class IngestionRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Audit record for a single ingestion run.

    Ingestion touches third-party services under specific licence terms, so
    every run is recorded: what was fetched, under which policy tier, how much
    was skipped, and what failed. This is both an operational dashboard and a
    compliance trail.
    """

    __tablename__ = "knowledge_ingestion_runs"
    __table_args__ = (
        Index("ix_ingestion_runs_source_started", "source", "started_at"),
    )

    source: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The UsageTier the run operated under, captured at run time.
    usage_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="running"
    )

    documents_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embeddings_created: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(UTC),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<IngestionRun {self.source} {self.status}>"
