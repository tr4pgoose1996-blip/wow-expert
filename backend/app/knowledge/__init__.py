"""The wow! expert. knowledge engine.

A retrieval-augmented question-answering system over World of Warcraft
knowledge, built around three principles:

**Licence compliance is enforced, not documented.** Every source is registered
in :mod:`app.knowledge.policy` with an evidence-backed usage tier. The tier is
enforced at the type boundary — :class:`~app.knowledge.documents.KnowledgeDocument`
silently discards prose from sources that have not licensed it, and refuses to
exist at all for disabled sources. A connector cannot opt itself out.

**Answers are grounded or refused.** Confidence is computed from retrieval
evidence *before* generation. When the evidence is too thin, the pipeline
declines to answer rather than producing fluent guesswork — and after
generation, any citation the model invented is stripped.

**Sources are pluggable.** A connector converts one external source into
:class:`KnowledgeDocument` objects; chunking, embedding, storage and retrieval
are all source-agnostic. Adding a source means writing one class.

Layout::

    policy.py       Source licence registry — the compliance boundary
    documents.py    Canonical document/chunk types
    chunking.py     Structure-aware splitting
    embeddings.py   Embedding provider abstraction
    vector_store.py pgvector persistence + hybrid retrieval
    confidence.py   Citation rendering and confidence estimation
    rag.py          The question-answering pipeline
    ingestion.py    Connector output to searchable index
    connectors/     One module per source
"""

from app.knowledge.chunking import ChunkingConfig, chunk_document
from app.knowledge.confidence import (
    Citation,
    ConfidenceAssessment,
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
from app.knowledge.embeddings import EmbeddingProvider, get_embedding_provider
from app.knowledge.ingestion import IngestionPipeline, IngestionResult
from app.knowledge.policy import (
    SOURCE_POLICIES,
    PolicyViolationError,
    SourceKey,
    SourcePolicy,
    UsageTier,
)
from app.knowledge.rag import Answer, RagPipeline
from app.knowledge.vector_store import PgVectorStore, SearchFilters, VectorStore

__all__ = [
    "SOURCE_POLICIES",
    "Answer",
    "ChunkingConfig",
    "Citation",
    "ConfidenceAssessment",
    "ConfidenceLevel",
    "DocumentChunk",
    "EmbeddingProvider",
    "EntityType",
    "IngestionPipeline",
    "IngestionResult",
    "KnowledgeDocument",
    "PgVectorStore",
    "PolicyViolationError",
    "RagPipeline",
    "RetrievedChunk",
    "SearchFilters",
    "SourceKey",
    "SourcePolicy",
    "UsageTier",
    "VectorStore",
    "assess_confidence",
    "build_citations",
    "chunk_document",
    "get_embedding_provider",
]
