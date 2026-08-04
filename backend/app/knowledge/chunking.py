"""Structure-aware document chunking.

Naive fixed-width chunking destroys the very structure that makes WoW content
answerable — a boss page's "Phase Two" section is a semantic unit, and cutting
it in half produces two chunks that each answer the question badly.

This chunker splits on heading boundaries first, then packs paragraphs into
token-budgeted windows with overlap, preserving the heading path so citations
can say *where* in a page an answer came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.knowledge.documents import DocumentChunk, KnowledgeDocument

__all__ = ["ChunkingConfig", "chunk_document"]

#: Markdown-style heading, e.g. "== Strategy ==" (wiki) or "## Strategy".
_HEADING = re.compile(r"^(?:(#{1,6})\s+(.+?)|(={2,6})\s*(.+?)\s*\3)\s*$")

#: Approximate characters per token for English prose. Good enough for
#: budgeting; the embedder enforces the real limit.
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Tunable chunking parameters.

    Defaults target ~1.6k characters, which sits comfortably inside the
    context window of every embedding model we support while remaining large
    enough to hold a complete boss ability or quest objective block.
    """

    target_tokens: int = 400
    max_tokens: int = 512
    overlap_tokens: int = 60
    #: Chunks below this are merged into their neighbour rather than stored;
    #: tiny fragments pollute retrieval with high-similarity noise.
    min_tokens: int = 24

    def __post_init__(self) -> None:
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap_tokens must be smaller than target_tokens")
        if self.max_tokens < self.target_tokens:
            raise ValueError("max_tokens must be >= target_tokens")

    @property
    def target_chars(self) -> int:
        return self.target_tokens * _CHARS_PER_TOKEN

    @property
    def max_chars(self) -> int:
        return self.max_tokens * _CHARS_PER_TOKEN

    @property
    def overlap_chars(self) -> int:
        return self.overlap_tokens * _CHARS_PER_TOKEN

    @property
    def min_chars(self) -> int:
        return self.min_tokens * _CHARS_PER_TOKEN


@dataclass(slots=True)
class _Section:
    """A run of text under a single heading path."""

    heading_path: list[str]
    text: str


def _split_sections(body: str) -> list[_Section]:
    """Split body text into sections keyed by their heading path."""
    sections: list[_Section] = []
    path: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            sections.append(_Section(heading_path=list(path), text=text))
        buffer.clear()

    for line in body.split("\n"):
        match = _HEADING.match(line)
        if match is None:
            buffer.append(line)
            continue

        flush()
        if match.group(1) is not None:  # markdown "###"
            level = len(match.group(1))
            title = match.group(2).strip()
        else:  # wiki "=== ... ==="
            level = len(match.group(3))
            title = match.group(4).strip()

        # Level 1/2 are the page title in both dialects; treat as depth 1.
        depth = max(0, level - 2)
        del path[depth:]
        path.append(title)

    flush()
    return sections


def _split_paragraphs(text: str) -> list[str]:
    """Split into paragraphs, further splitting any that exceed the budget."""
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def _hard_split(paragraph: str, max_chars: int) -> list[str]:
    """Split an oversized paragraph on sentence boundaries, then hard-wrap."""
    if len(paragraph) <= max_chars:
        return [paragraph]

    pieces: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
        if not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= max_chars:
            current = f"{current} {sentence}"
        else:
            pieces.append(current)
            current = sentence
    if current:
        pieces.append(current)

    # Any single sentence still too long (tables, stat blocks) is hard-wrapped.
    wrapped: list[str] = []
    for piece in pieces:
        while len(piece) > max_chars:
            cut = piece.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            wrapped.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            wrapped.append(piece)
    return wrapped


def _tail_overlap(text: str, overlap_chars: int) -> str:
    """Return the trailing context carried into the next chunk.

    Cut on a sentence boundary where possible so the overlap reads as
    coherent prose rather than a mid-word fragment.
    """
    if overlap_chars <= 0 or len(text) <= overlap_chars:
        return text if len(text) <= overlap_chars else ""
    tail = text[-overlap_chars:]
    match = re.search(r"(?<=[.!?])\s+", tail)
    if match:
        return tail[match.end():].strip()
    space = tail.find(" ")
    return tail[space + 1:].strip() if space != -1 else tail.strip()


def chunk_document(
    document: KnowledgeDocument,
    config: ChunkingConfig | None = None,
) -> list[DocumentChunk]:
    """Split ``document`` into embeddable chunks.

    Documents with no prose (metadata-only sources, or short API records)
    still yield exactly one chunk built from the title and structured facts,
    so that every ingested entity remains retrievable.

    Args:
        document: The normalised source document.
        config: Chunking parameters; defaults are used when omitted.

    Returns:
        Ordered chunks. Never empty for a valid document.
    """
    cfg = config or ChunkingConfig()

    # A header line repeated on every chunk keeps each one self-describing,
    # which measurably improves retrieval on short fragments.
    header = f"{document.title} ({document.entity_type.value})"
    facts_text = ""
    if document.facts:
        facts_text = "; ".join(
            f"{k.replace('_', ' ')}: {v}"
            for k, v in document.facts.items()
            if v not in (None, "", [], {})
        )

    if not document.body:
        text = header if not facts_text else f"{header}\n{facts_text}"
        return [
            DocumentChunk(
                document_key=document.stable_key,
                ordinal=0,
                text=text,
                source=document.source,
                entity_type=document.entity_type,
                title=document.title,
                url=document.url,
                game_version=document.game_version,
            )
        ]

    chunks: list[DocumentChunk] = []
    ordinal = 0
    # Facts ride along in the first chunk so they are always retrievable.
    pending_prefix = facts_text

    for section in _split_sections(document.body):
        buffer = ""
        for paragraph in _split_paragraphs(section.text):
            for piece in _hard_split(paragraph, cfg.max_chars):
                candidate = f"{buffer}\n\n{piece}".strip() if buffer else piece
                if len(candidate) <= cfg.target_chars:
                    buffer = candidate
                    continue

                if buffer:
                    chunks.append(
                        _build_chunk(
                            document, ordinal, header, pending_prefix,
                            buffer, section.heading_path,
                        )
                    )
                    ordinal += 1
                    pending_prefix = ""
                    overlap = _tail_overlap(buffer, cfg.overlap_chars)
                    buffer = f"{overlap}\n\n{piece}".strip()
                else:
                    buffer = piece

        if buffer:
            # Merge a runt tail into the previous chunk when it belongs to the
            # same section, rather than emitting a near-useless fragment.
            if (
                len(buffer) < cfg.min_chars
                and chunks
                and chunks[-1].heading_path == section.heading_path
                and len(chunks[-1].text) + len(buffer) <= cfg.max_chars
            ):
                chunks[-1].text = f"{chunks[-1].text}\n\n{buffer}"
            else:
                chunks.append(
                    _build_chunk(
                        document, ordinal, header, pending_prefix,
                        buffer, section.heading_path,
                    )
                )
                ordinal += 1
                pending_prefix = ""

    if not chunks:  # body was whitespace or headings only
        text = header if not facts_text else f"{header}\n{facts_text}"
        chunks.append(
            DocumentChunk(
                document_key=document.stable_key,
                ordinal=0,
                text=text,
                source=document.source,
                entity_type=document.entity_type,
                title=document.title,
                url=document.url,
                game_version=document.game_version,
            )
        )
    return chunks


def _build_chunk(
    document: KnowledgeDocument,
    ordinal: int,
    header: str,
    prefix: str,
    body: str,
    heading_path: list[str],
) -> DocumentChunk:
    """Assemble one chunk with its self-describing header."""
    parts = [header]
    if heading_path:
        parts.append(" > ".join(heading_path))
    if prefix:
        parts.append(prefix)
    parts.append(body)
    return DocumentChunk(
        document_key=document.stable_key,
        ordinal=ordinal,
        text="\n".join(parts),
        source=document.source,
        entity_type=document.entity_type,
        title=document.title,
        url=document.url,
        game_version=document.game_version,
        heading_path=list(heading_path),
    )
