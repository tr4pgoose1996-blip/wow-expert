"""Citation rendering and answer confidence estimation.

Two requirements drive this module:

1. **Citations** — every factual claim must be traceable to a source, with the
   licence attribution that source requires.
2. **Confidence** — the system must say how much to trust an answer. A
   confident-sounding wrong answer about a raid mechanic wastes a player's
   evening; the engine is explicitly built to say "I'm not sure" instead.

Confidence is computed from retrieval evidence *before* generation, so it
reflects what was actually found rather than the model's self-assessment —
which is well known to be poorly calibrated.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import StrEnum

from app.knowledge.documents import RetrievedChunk
from app.knowledge.policy import SourceKey, get_policy

__all__ = [
    "Citation",
    "ConfidenceAssessment",
    "ConfidenceLevel",
    "assess_confidence",
    "build_citations",
]


class ConfidenceLevel(StrEnum):
    """Coarse confidence bands surfaced to the client."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    #: Evidence was too weak to answer at all.
    INSUFFICIENT = "insufficient"

    @property
    def should_answer(self) -> bool:
        return self is not ConfidenceLevel.INSUFFICIENT

    @property
    def guidance(self) -> str:
        """Plain-language caveat shown alongside the answer."""
        return {
            ConfidenceLevel.HIGH: (
                "Well supported by authoritative sources."
            ),
            ConfidenceLevel.MEDIUM: (
                "Supported by the sources found, but verify anything "
                "version-sensitive in game."
            ),
            ConfidenceLevel.LOW: (
                "Weakly supported. Treat this as a starting point and "
                "confirm against the linked sources."
            ),
            ConfidenceLevel.INSUFFICIENT: (
                "Not enough indexed material to answer reliably."
            ),
        }[self]


@dataclass(frozen=True, slots=True)
class Citation:
    """A single reference backing an answer."""

    index: int
    title: str
    url: str
    source: SourceKey
    source_name: str
    attribution: str
    entity_type: str
    location: str
    relevance: float
    #: Verbatim excerpt. Populated only where the licence permits quotation;
    #: empty for metadata-only sources, which are cited by link alone.
    excerpt: str = ""

    @property
    def marker(self) -> str:
        """Inline marker the model is instructed to use, e.g. ``[1]``."""
        return f"[{self.index}]"


@dataclass(slots=True)
class ConfidenceAssessment:
    """The full confidence picture for one answer."""

    level: ConfidenceLevel
    #: Continuous score in [0, 1] behind the band.
    score: float
    #: Human-readable reasons, surfaced in the API for transparency.
    reasons: list[str] = field(default_factory=list)
    #: Component breakdown, for tuning and debugging.
    signals: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "level": self.level.value,
            "score": round(self.score, 3),
            "guidance": self.level.guidance,
            "reasons": self.reasons,
            "signals": {k: round(v, 3) for k, v in self.signals.items()},
        }


#: Longest excerpt reproduced from a full-text source. Kept short so that
#: citations remain fair-use-sized quotations, not redistribution.
_MAX_EXCERPT_CHARS = 320


def _excerpt(text: str, limit: int = _MAX_EXCERPT_CHARS) -> str:
    """Trim to a readable excerpt on a word boundary."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed.rfind(" ", 0, limit)
    return collapsed[: cut if cut > 0 else limit].rstrip() + "\u2026"


def build_citations(retrieved: list[RetrievedChunk]) -> list[Citation]:
    """Turn retrieved chunks into numbered citations.

    Chunks from the same document collapse into one citation — a user wants
    "the Warcraft Wiki page on Ragnaros", not three references to it. The
    highest-scoring chunk supplies the excerpt.

    Args:
        retrieved: Ranked chunks from the vector store.

    Returns:
        Citations numbered from 1, ordered by relevance.
    """
    seen: dict[str, Citation] = {}
    for item in retrieved:
        chunk = item.chunk
        key = chunk.document_key
        if key in seen:
            continue

        policy = get_policy(chunk.source)
        seen[key] = Citation(
            index=len(seen) + 1,
            title=chunk.title,
            url=chunk.url,
            source=chunk.source,
            source_name=policy.display_name,
            attribution=policy.attribution,
            entity_type=chunk.entity_type.value,
            location=chunk.display_location,
            relevance=round(item.score, 4),
            excerpt=(
                _excerpt(chunk.text) if policy.tier.allows_quotation else ""
            ),
        )
    return list(seen.values())


# -- Confidence thresholds -------------------------------------------------
# Tuned so that "no relevant content indexed" reliably lands in INSUFFICIENT
# rather than producing a fluent answer from unrelated chunks.

_MIN_EVIDENCE_SCORE = 0.35
_HIGH_THRESHOLD = 0.72
_MEDIUM_THRESHOLD = 0.50
_LOW_THRESHOLD = 0.28


def assess_confidence(
    retrieved: list[RetrievedChunk],
    *,
    question: str = "",
) -> ConfidenceAssessment:
    """Estimate answer confidence from retrieval evidence.

    Four signals are combined:

    * **Top relevance** — how good the single best match is. Dominant signal:
      one strongly matching passage usually means the answer is present.
    * **Corroboration** — how many independent documents support the topic.
      Agreement across sources is the strongest guard against a single stale
      or wrong page.
    * **Authority** — whether Blizzard's own API backs the answer. First-party
      data is definitionally correct about game state.
    * **Agreement** — score spread among top hits. A tight cluster suggests
      consistent evidence; a sharp cliff suggests one lucky match.

    Args:
        retrieved: Ranked chunks backing the answer.
        question: The original question, used only for reason text.

    Returns:
        The assessment, including a breakdown of each signal.
    """
    if not retrieved:
        return ConfidenceAssessment(
            level=ConfidenceLevel.INSUFFICIENT,
            score=0.0,
            reasons=["No indexed content matched this question."],
            signals={},
        )

    # Absolute evidence quality, NOT fused rank.
    #
    # Reciprocal Rank Fusion scores encode *position*, not match quality: the
    # best of a uniformly terrible candidate set still lands at rank 1 and
    # therefore carries the maximum RRF score. Deriving confidence from rank
    # would mark every answerable-looking question as well supported, because
    # near-any question retrieves something. So the dominant signal here is
    # the strongest absolute similarity actually observed — cosine similarity
    # from the dense retriever, or normalised lexical overlap — which does
    # fall towards zero when nothing genuinely matches.
    absolute = [
        max(item.vector_score, item.lexical_score) for item in retrieved
    ]
    top_relevance = max(absolute)
    # Rank order still decides *which* chunks are considered; it just no
    # longer decides how much we trust them.
    relevances = absolute

    # Only chunks that actually match count as corroboration. Three unrelated
    # documents are not three pieces of evidence.
    supporting = [
        item
        for item, relevance in zip(retrieved, absolute, strict=True)
        if relevance >= _MIN_EVIDENCE_SCORE
    ]
    distinct_documents = len({item.chunk.document_key for item in supporting})
    distinct_sources = {item.chunk.source for item in supporting}
    # Three or more supporting documents is treated as full corroboration;
    # beyond that, additional hits add little independent evidence.
    corroboration = min(1.0, distinct_documents / 3.0)

    # Authority only counts when the authoritative chunk is itself a match.
    has_authority = any(item.is_authoritative for item in supporting)
    authority = 1.0 if has_authority else 0.45

    top_slice = relevances[: min(5, len(relevances))]
    if len(top_slice) > 1:
        spread = statistics.pstdev(top_slice)
        # Low spread across strong hits == consistent evidence.
        agreement = max(0.0, 1.0 - min(1.0, spread * 2.5))
    else:
        agreement = 0.5

    signals = {
        "top_relevance": top_relevance,
        "corroboration": corroboration,
        "authority": authority,
        "agreement": agreement,
    }
    score = (
        0.45 * top_relevance
        + 0.25 * corroboration
        + 0.20 * authority
        + 0.10 * agreement
    )

    reasons: list[str] = []
    if top_relevance < _MIN_EVIDENCE_SCORE:
        reasons.append(
            "The best matching passage is only weakly related to the question."
        )
    elif distinct_documents == 1:
        reasons.append("Only one document supports this answer.")
    else:
        reasons.append(
            f"{distinct_documents} documents across "
            f"{len(distinct_sources)} source(s) support this answer."
        )
    if has_authority:
        reasons.append("Backed by first-party Blizzard game data.")
    else:
        reasons.append(
            "No first-party Blizzard data matched; based on community sources."
        )

    # Hard floor: fluent nonsense from unrelated chunks is the failure mode
    # this guard exists to prevent. Nothing that clears no real evidence
    # threshold may be answered, regardless of how the weights combine.
    if top_relevance < _MIN_EVIDENCE_SCORE:
        level = ConfidenceLevel.INSUFFICIENT
    elif score >= _HIGH_THRESHOLD:
        level = ConfidenceLevel.HIGH
    elif score >= _MEDIUM_THRESHOLD:
        level = ConfidenceLevel.MEDIUM
    elif score >= _LOW_THRESHOLD:
        level = ConfidenceLevel.LOW
    else:
        level = ConfidenceLevel.INSUFFICIENT

    # Game data changes every patch; community prose ages badly. Flag it.
    if level is ConfidenceLevel.HIGH and not has_authority:
        reasons.append(
            "Community sources can lag behind the current patch."
        )

    return ConfidenceAssessment(
        level=level, score=score, reasons=reasons, signals=signals
    )
