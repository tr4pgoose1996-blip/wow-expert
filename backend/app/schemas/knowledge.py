"""Request and response schemas for the knowledge engine API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.knowledge.confidence import ConfidenceLevel
from app.knowledge.documents import EntityType
from app.knowledge.policy import SourceKey

__all__ = [
    "AskRequest",
    "AskResponse",
    "CitationOut",
    "ConfidenceOut",
    "IndexStatsResponse",
    "IngestRequest",
    "IngestResponse",
    "SearchHit",
    "SearchRequest",
    "SearchResponse",
    "SourcePolicyOut",
]


class AskRequest(BaseModel):
    """A question for the Game Master."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "question": "What are the phases of the Ragnaros encounter?",
                "top_k": 8,
                "entity_types": ["boss"],
            }
        }
    )

    question: str = Field(
        min_length=3,
        max_length=1000,
        description="The player's question, in natural language.",
    )
    top_k: int = Field(
        default=8, ge=1, le=25,
        description="How many passages to retrieve as evidence.",
    )
    sources: list[SourceKey] | None = Field(
        default=None,
        description="Restrict retrieval to these sources. Omit for all.",
    )
    entity_types: list[EntityType] | None = Field(
        default=None,
        description="Restrict retrieval to these entity types.",
    )
    game_version: str | None = Field(
        default=None,
        max_length=32,
        description=(
            "Restrict to 'retail' or 'classic'. Inferred from the question "
            "when omitted."
        ),
    )
    character_id: str | None = Field(
        default=None,
        description=(
            "Optional character to tailor advice to. Must belong to the "
            "authenticated user."
        ),
    )

    @field_validator("question")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("question must not be blank")
        return cleaned


class CitationOut(BaseModel):
    """A source reference backing part of an answer."""

    index: int = Field(description="Marker number used inline, e.g. [1].")
    title: str
    url: str
    source: str
    source_name: str
    attribution: str = Field(
        description="Licence attribution that must be displayed with this citation."
    )
    entity_type: str
    location: str = Field(description="Section within the source document.")
    relevance: float
    excerpt: str = Field(
        default="",
        description=(
            "Verbatim excerpt. Empty where the source's licence does not "
            "permit quotation; follow the URL instead."
        ),
    )


class ConfidenceOut(BaseModel):
    """How much to trust the answer."""

    level: ConfidenceLevel
    score: float = Field(ge=0.0, le=1.0)
    guidance: str = Field(description="Plain-language caveat for the user.")
    reasons: list[str]
    signals: dict[str, float] = Field(
        description="Component scores behind the assessment."
    )


class AskResponse(BaseModel):
    """A grounded, cited answer."""

    question: str
    answer: str
    refused: bool = Field(
        description=(
            "True when the engine declined to answer for lack of evidence. "
            "The answer field then explains why."
        )
    )
    confidence: ConfidenceOut
    citations: list[CitationOut]
    retrieved_count: int
    latency_ms: int
    model: str
    filters_applied: dict[str, Any]
    warnings: list[str] = Field(
        default_factory=list,
        description="Issues detected during generation, such as invalid citations.",
    )


class SearchRequest(BaseModel):
    """A retrieval-only lookup."""

    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=20, ge=1, le=100)
    sources: list[SourceKey] | None = None
    entity_types: list[EntityType] | None = None
    game_version: str | None = Field(default=None, max_length=32)


class SearchHit(BaseModel):
    """One retrieved passage."""

    title: str
    url: str
    source: str
    entity_type: str
    location: str
    game_version: str
    score: float
    vector_score: float
    lexical_score: float
    rank: int
    excerpt: str


class SearchResponse(BaseModel):
    query: str
    total: int
    results: list[SearchHit]


class IngestRequest(BaseModel):
    """Trigger ingestion for one source. Admin only."""

    source: SourceKey
    dry_run: bool = Field(
        default=False,
        description="Parse and chunk without writing or embedding.",
    )
    limit: int | None = Field(
        default=None, ge=1,
        description="Cap records per entity type. Useful for smoke tests.",
    )


class IngestResponse(BaseModel):
    source: str
    tier: str
    status: str
    documents_seen: int
    documents_written: int
    documents_skipped: int
    chunks_written: int
    embeddings_created: int
    duration_seconds: float
    errors: list[str]


class SourcePolicyOut(BaseModel):
    """The licence terms a source is operated under."""

    display_name: str
    tier: str
    licence: str
    attribution: str
    enabled: bool
    allows_quotation: bool
    homepage: str


class IndexStatsResponse(BaseModel):
    """Index contents and source policies."""

    sources: dict[str, Any]
    total_chunks: int
    embedded_chunks: int
    embedding_model: str
    embedding_dimensions: int
    policies: dict[str, SourcePolicyOut]
