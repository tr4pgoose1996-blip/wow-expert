"""Canonical document and chunk types for the knowledge engine.

Every connector, regardless of source, normalises into :class:`KnowledgeDocument`.
Downstream stages (chunking, embedding, retrieval, answering) know only these
types, which is what lets a new source be added without touching the pipeline.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from app.knowledge.policy import SourceKey, UsageTier, get_policy

__all__ = [
    "DocumentChunk",
    "EntityType",
    "KnowledgeDocument",
    "RetrievedChunk",
]


class EntityType(StrEnum):
    """The kinds of WoW entity the engine can answer about.

    Adding a new entity type requires no pipeline changes — only a connector
    that emits it and, optionally, a retrieval boost rule.
    """

    QUEST = "quest"
    NPC = "npc"
    ZONE = "zone"
    ITEM = "item"
    BOSS = "boss"
    DUNGEON = "dungeon"
    RAID = "raid"
    PROFESSION = "profession"
    ACHIEVEMENT = "achievement"
    MOUNT = "mount"
    PET = "pet"
    SPELL = "spell"
    CLASS_SPEC = "class_spec"
    REPUTATION = "reputation"
    GUIDE = "guide"
    LORE = "lore"
    OTHER = "other"


_WHITESPACE = re.compile(r"[ \t\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def normalise_text(raw: str) -> str:
    """Collapse redundant whitespace while preserving paragraph structure.

    Chunk boundaries and embedding quality both degrade badly on the ragged
    whitespace that wiki and HTML sources produce, so this runs on every
    document at construction time.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


@dataclass(slots=True)
class KnowledgeDocument:
    """A single normalised unit of knowledge from any source.

    Attributes:
        source: Which registered source produced this. Determines licence.
        source_id: The source's own stable identifier (quest id, page id).
        entity_type: What kind of game entity this describes.
        title: Human-readable name, used for lexical matching and citations.
        url: Canonical link, surfaced in citations.
        body: Prose content. Empty for METADATA_ONLY sources.
        facts: Structured key/value data. Always permitted where the source
            tier is STRUCTURED_FACTS or better.
        game_version: e.g. ``"retail"``, ``"classic-era"``. Prevents a Classic
            answer being served for a retail question.
        patch: Game patch this content was accurate for.
        language: BCP-47 locale of ``body``.
        updated_at: When the source last modified the content.
    """

    source: SourceKey
    source_id: str
    entity_type: EntityType
    title: str
    url: str
    body: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    game_version: str = "retail"
    patch: str | None = None
    language: str = "en_US"
    updated_at: datetime | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        self.title = self.title.strip()
        self.body = normalise_text(self.body)
        if not self.title:
            raise ValueError(
                f"Document {self.source}:{self.source_id} has no title; "
                "titles are required for citation rendering."
            )
        self._enforce_policy()

    def _enforce_policy(self) -> None:
        """Drop content the source has not licensed us to retain.

        Enforced here — at the type boundary — so that no connector can
        accidentally route unlicensed prose into the store, however it was
        constructed.
        """
        policy = get_policy(self.source)
        if policy.tier is UsageTier.DISABLED:
            from app.knowledge.policy import PolicyViolationError

            raise PolicyViolationError(
                f"Refusing to construct a document from "
                f"{policy.display_name}: {policy.rationale}"
            )
        if not policy.tier.allows_text_storage:
            # Metadata/structured tiers keep the title and facts, never prose.
            self.body = ""

    @property
    def stable_key(self) -> str:
        """Deterministic identity used for idempotent upserts."""
        return f"{self.source.value}:{self.game_version}:{self.source_id}"

    @property
    def content_hash(self) -> str:
        """Hash of everything that would change the embedding.

        Re-embedding is the expensive part of ingestion, so a run compares
        this against the stored hash and skips unchanged documents.
        """
        digest = hashlib.blake2b(digest_size=16)
        digest.update(self.title.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(self.body.encode("utf-8"))
        digest.update(b"\x00")
        for key in sorted(self.facts):
            digest.update(f"{key}={self.facts[key]!r}".encode())
        return digest.hexdigest()

    @property
    def attribution(self) -> str:
        """Licence attribution that must accompany any citation."""
        return get_policy(self.source).attribution

    def searchable_text(self) -> str:
        """The full text presented to the embedder.

        Title and structured facts are folded into the embedded text so that
        metadata-only documents remain retrievable despite having no prose.
        """
        parts = [self.title]
        if self.facts:
            parts.append(
                "; ".join(
                    f"{k.replace('_', ' ')}: {v}"
                    for k, v in self.facts.items()
                    if v not in (None, "", [], {})
                )
            )
        if self.body:
            parts.append(self.body)
        return "\n\n".join(p for p in parts if p)


@dataclass(slots=True)
class DocumentChunk:
    """An embeddable slice of a document.

    Chunks carry a denormalised copy of their parent's citation fields so that
    rendering an answer never requires a second database round-trip.
    """

    document_key: str
    ordinal: int
    text: str
    source: SourceKey
    entity_type: EntityType
    title: str
    url: str
    game_version: str = "retail"
    #: Heading path within the document, e.g. ``["Strategy", "Phase Two"]``.
    heading_path: list[str] = field(default_factory=list)
    embedding: list[float] | None = None

    @property
    def chunk_key(self) -> str:
        return f"{self.document_key}#{self.ordinal}"

    @property
    def token_estimate(self) -> int:
        """Cheap token estimate (~4 chars/token) for context budgeting."""
        return max(1, len(self.text) // 4)

    @property
    def display_location(self) -> str:
        """Where in the document this chunk came from, for citations."""
        if not self.heading_path:
            return self.title
        separator = " \u203a "
        return separator.join([self.title, *self.heading_path])


@dataclass(slots=True)
class RetrievedChunk:
    """A chunk returned by retrieval, with its scoring breakdown.

    The separate score components are retained rather than collapsed because
    the confidence model reads them individually, and because they make
    retrieval quality debuggable in production.
    """

    chunk: DocumentChunk
    #: Cosine similarity in [0, 1] from the vector index.
    vector_score: float = 0.0
    #: Lexical BM25-style score from Postgres full-text search, normalised.
    lexical_score: float = 0.0
    #: Fused score actually used for ranking.
    score: float = 0.0
    #: Rank after fusion, 1-based.
    rank: int = 0

    @classmethod
    def from_chunk(cls, chunk: DocumentChunk, **scores: float) -> Self:
        return cls(chunk=chunk, **scores)

    @property
    def is_authoritative(self) -> bool:
        """Whether this came from Blizzard's own API."""
        return self.chunk.source is SourceKey.BLIZZARD_GAME_DATA
