"""Knowledge engine tables with pgvector and full-text search.

Creates the vector extension, the document/chunk/audit tables, a generated
tsvector column for lexical search, and both the GIN and HNSW indexes that
hybrid retrieval depends on.

Revision ID: 0003_knowledge_engine
Revises: 0002_blizzard_integration
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_knowledge_engine"
down_revision = "0002_blizzard_integration"
branch_labels = None
depends_on = None

#: Must match settings.EMBEDDING_DIMENSIONS. Hard-coded here because a
#: migration must be reproducible regardless of current environment config.
EMBEDDING_DIMENSIONS = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_documents",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True, server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("stable_key", sa.String(512), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("entity_type", sa.String(48), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "facts", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "game_version", sa.String(32), nullable=False,
            server_default="retail",
        ),
        sa.Column("patch", sa.String(32), nullable=True),
        sa.Column(
            "language", sa.String(16), nullable=False, server_default="en_US"
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(128), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("stable_key", name="uq_knowledge_documents_stable_key"),
    )
    op.create_index(
        "ix_knowledge_documents_source_entity",
        "knowledge_documents", ["source", "entity_type"],
    )
    op.create_index(
        "ix_knowledge_documents_game_version",
        "knowledge_documents", ["game_version"],
    )
    op.create_index(
        "ix_knowledge_documents_refresh",
        "knowledge_documents", ["source", "fetched_at"],
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True, server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "document_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(48), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False, server_default=""),
        sa.Column(
            "game_version", sa.String(32), nullable=False,
            server_default="retail",
        ),
        sa.Column(
            "heading_path", postgresql.JSONB(),
            nullable=False, server_default="[]",
        ),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "document_id", "ordinal", name="uq_knowledge_chunks_doc_ordinal"
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"]
    )
    op.create_index(
        "ix_knowledge_chunks_source_entity",
        "knowledge_chunks", ["source", "entity_type"],
    )
    op.create_index(
        "ix_knowledge_chunks_game_version", "knowledge_chunks", ["game_version"]
    )

    # A generated column keeps the tsvector correct by construction — there is
    # no code path that can write chunk text and forget to refresh the index.
    # Title is weighted 'A' above body 'B' so that a proper-noun match in the
    # title outranks an incidental mention in prose.
    op.execute(
        """
        ALTER TABLE knowledge_chunks
        ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(text, '')), 'B')
        ) STORED
        """
    )
    op.execute(
        """
        CREATE INDEX ix_knowledge_chunks_search_vector
        ON knowledge_chunks USING GIN (search_vector)
        """
    )

    # HNSW over cosine distance: higher build cost than IVFFlat but far better
    # recall/latency at query time, and no need to retrain as rows are added.
    op.execute(
        """
        CREATE INDEX ix_knowledge_chunks_embedding_hnsw
        ON knowledge_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )

    op.create_table(
        "knowledge_ingestion_runs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True, server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("usage_tier", sa.String(32), nullable=False),
        sa.Column(
            "status", sa.String(24), nullable=False, server_default="running"
        ),
        sa.Column(
            "documents_seen", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "documents_written", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "documents_skipped", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "chunks_written", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "embeddings_created", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_ingestion_runs_source_started",
        "knowledge_ingestion_runs", ["source", "started_at"],
    )


def downgrade() -> None:
    op.drop_table("knowledge_ingestion_runs")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_search_vector")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
    # The vector extension is left in place: other schemas may depend on it,
    # and dropping it is not reversible without data loss.
