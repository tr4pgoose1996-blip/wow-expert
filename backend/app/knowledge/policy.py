"""Source licensing policy for the knowledge engine.

This module is the compliance boundary of the entire ingestion pipeline.

Every knowledge source is registered here with an explicit, evidence-backed
:class:`UsageTier`. The ingestion pipeline consults this registry *before*
persisting anything, and silently downgrades or refuses content that a source
has not licensed for our use. Connectors cannot opt themselves out: the
registry is the single authority.

Rationale for each entry is recorded inline with the evidence it came from
(robots.txt directives, published licences, terms of use) so that a future
maintainer can re-verify rather than guess. Re-verify these annually, or
whenever a source changes its terms — see ``scripts/verify_source_policy.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

__all__ = [
    "SOURCE_POLICIES",
    "PolicyViolationError",
    "SourceKey",
    "SourcePolicy",
    "UsageTier",
    "assert_text_storage_allowed",
    "get_policy",
]


class PolicyViolationError(RuntimeError):
    """Raised when a connector attempts something its source forbids.

    This is deliberately a hard error rather than a warning: a silent
    compliance failure is far more expensive than a failed ingestion run.
    """


class UsageTier(StrEnum):
    """How much of a source we are permitted to retain and serve.

    The tiers form a strict ladder of decreasing permission.
    """

    #: Full document text may be stored, embedded, chunked and quoted.
    FULL_TEXT = "full_text"
    #: Structured factual fields only (names, ids, numeric stats). Free-text
    #: prose such as guides and strategy write-ups must not be retained.
    STRUCTURED_FACTS = "structured_facts"
    #: Only titles, URLs and short factual descriptors. Answers may cite and
    #: link to the source but must never reproduce its body text.
    METADATA_ONLY = "metadata_only"
    #: Ingestion is refused outright.
    DISABLED = "disabled"

    @property
    def allows_text_storage(self) -> bool:
        """Whether prose from this source may be persisted and embedded."""
        return self is UsageTier.FULL_TEXT

    @property
    def allows_quotation(self) -> bool:
        """Whether an answer may quote body text verbatim."""
        return self is UsageTier.FULL_TEXT


class SourceKey(StrEnum):
    """Stable identifiers for every registered knowledge source."""

    BLIZZARD_GAME_DATA = "blizzard_game_data"
    WARCRAFT_WIKI = "warcraft_wiki"
    RAIDER_IO = "raider_io"
    WOWHEAD = "wowhead"
    ICY_VEINS = "icy_veins"
    METHOD = "method"
    PETOPIA = "petopia"


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    """The licence terms we operate a single source under."""

    key: SourceKey
    display_name: str
    homepage: str
    tier: UsageTier
    #: Human-readable licence identifier, e.g. ``"CC BY-SA 4.0"``.
    licence: str
    #: Why this tier was chosen, citing the evidence.
    rationale: str
    #: Attribution string that MUST accompany any answer citing this source.
    attribution: str
    #: Whether derived embeddings may be used to fine-tune/train a model.
    #: Distinct from RAG retrieval, which several sources permit while
    #: forbidding training (``ai-train=no``).
    allows_model_training: bool = False
    #: Politeness floor between HTTP requests, in seconds.
    min_request_interval: float = 1.0
    #: User-Agent we identify ourselves with. Honest identification is a
    #: precondition of good-faith crawling.
    user_agent: str = (
        "wow-expert-knowledge-engine/1.0 "
        "(+https://github.com/wow-expert/wow-expert; contact@wow-expert.app)"
    )
    #: Extra machine-readable notes for operators.
    notes: Mapping[str, str] = field(default_factory=dict)

    @property
    def requires_share_alike(self) -> bool:
        """CC BY-SA content obliges us to license derivatives alike."""
        return "BY-SA" in self.licence.upper()


# --------------------------------------------------------------------------
# The registry.
#
# Evidence gathered by direct inspection of each source's robots.txt and
# published terms. Dates are the last manual verification.
# --------------------------------------------------------------------------

_POLICIES: dict[SourceKey, SourcePolicy] = {
    SourceKey.BLIZZARD_GAME_DATA: SourcePolicy(
        key=SourceKey.BLIZZARD_GAME_DATA,
        display_name="Blizzard Battle.net Game Data API",
        homepage="https://develop.battle.net/documentation",
        tier=UsageTier.FULL_TEXT,
        licence="Blizzard API Terms of Use (developer account)",
        rationale=(
            "First-party, officially documented API accessed with our own "
            "registered client credentials. Blizzard's API Terms permit "
            "building non-commercial and approved applications on the "
            "returned data. This is the authoritative source and is always "
            "preferred over any third party for the same fact."
        ),
        attribution=(
            "Game data \u00a9 Blizzard Entertainment, Inc., retrieved via the "
            "Battle.net Game Data API."
        ),
        allows_model_training=False,
        min_request_interval=0.0,  # governed by the API's own rate limiter
        notes={"verified": "2026-08-02", "authoritative": "true"},
    ),
    SourceKey.WARCRAFT_WIKI: SourcePolicy(
        key=SourceKey.WARCRAFT_WIKI,
        display_name="Warcraft Wiki (warcraft.wiki.gg)",
        homepage="https://warcraft.wiki.gg",
        tier=UsageTier.FULL_TEXT,
        licence="CC BY-SA 4.0",
        rationale=(
            "Warcraft Wiki:Copyrights licenses all lawfully licensable text "
            "under CC BY-SA 4.0, which permits redistribution and derivative "
            "works given attribution and share-alike. robots.txt sets "
            "'Content-Signal: search=yes, ai-train=no, use=reference' and "
            "'Allow: /' for the default agent — reference use (RAG grounding "
            "with citation) is within signalled permission; training is not. "
            "Access uses the MediaWiki Action API, not HTML scraping."
        ),
        attribution=(
            "Content from Warcraft Wiki (warcraft.wiki.gg), licensed "
            "CC BY-SA 4.0. Warcraft content is the property of Blizzard "
            "Entertainment, Inc."
        ),
        allows_model_training=False,  # ai-train=no
        min_request_interval=0.5,
        notes={
            "verified": "2026-08-02",
            "content_signal": "search=yes,ai-train=no,use=reference",
            "access_method": "MediaWiki Action API",
            "share_alike": "true",
        },
    ),
    SourceKey.RAIDER_IO: SourcePolicy(
        key=SourceKey.RAIDER_IO,
        display_name="Raider.IO",
        homepage="https://raider.io",
        tier=UsageTier.STRUCTURED_FACTS,
        licence="Raider.IO Terms of Use — public API exception",
        rationale=(
            "Raider.IO's Terms prohibit systematically retrieving content to "
            "compile a database and prohibit automated access, but both "
            "prohibitions carve out 'our publicly available API'. We "
            "therefore use ONLY the documented API at raider.io/api and never "
            "scrape site HTML. We retain structured scores and progression "
            "facts, not editorial prose. Unauthenticated API access is capped "
            "at 200 requests/minute. Commercial use requires their written "
            "permission — see notes."
        ),
        attribution="Mythic+ and raid progression data courtesy of Raider.IO.",
        allows_model_training=False,
        min_request_interval=0.31,  # ~193 req/min, under the 200/min cap
        notes={
            "verified": "2026-08-02",
            "api_only": "true",
            "rate_limit": "200/min unauthenticated",
            "commercial_use": (
                "Requires prior written permission from RaiderIO, Inc. "
                "Set KNOWLEDGE_COMMERCIAL_MODE=true only once obtained."
            ),
        },
    ),
    SourceKey.WOWHEAD: SourcePolicy(
        key=SourceKey.WOWHEAD,
        display_name="Wowhead",
        homepage="https://www.wowhead.com",
        tier=UsageTier.DISABLED,
        licence="All rights reserved — AI crawlers explicitly refused",
        rationale=(
            "wowhead.com/robots.txt issues 'Disallow: /' to a block of "
            "AI-related agents including ClaudeBot, Claude-Web, GPTBot, "
            "anthropic-ai, CCBot, cohere-ai, Google-Extended, Bytespider and "
            "Scrapy. This is an unambiguous refusal of exactly the use case "
            "an AI knowledge engine represents, and no public API or content "
            "licence offers an alternative route. The connector is therefore "
            "registered but hard-disabled. Do not enable it without written "
            "permission from Wowhead; enabling it in code alone will raise "
            "PolicyViolationError."
        ),
        attribution="",
        allows_model_training=False,
        notes={
            "verified": "2026-08-02",
            "robots_directive": "Disallow: / for AI user-agents",
            "override": "Requires written permission; contact Wowhead/ZAM.",
        },
    ),
    SourceKey.ICY_VEINS: SourcePolicy(
        key=SourceKey.ICY_VEINS,
        display_name="Icy Veins",
        homepage="https://www.icy-veins.com",
        tier=UsageTier.METADATA_ONLY,
        licence="All rights reserved",
        rationale=(
            "robots.txt permits general crawling ('Allow: /' with narrow "
            "Disallow paths for /wow/*/modules/, /util/, /sets/ etc.), so "
            "fetching is not refused. However Icy Veins grants no content "
            "licence, and their guides are original copyrighted editorial "
            "work. We therefore index only the title, canonical URL, class/"
            "spec taxonomy and last-updated date so we can point a user at "
            "the right guide. Guide body text is never stored or quoted."
        ),
        attribution="Guide index from Icy Veins (icy-veins.com).",
        allows_model_training=False,
        min_request_interval=2.0,
        notes={
            "verified": "2026-08-02",
            "disallowed_paths": "/wow/*/modules/,/wow/*/util/,/wow/*/sets/",
        },
    ),
    SourceKey.METHOD: SourcePolicy(
        key=SourceKey.METHOD,
        display_name="Method",
        homepage="https://www.method.gg",
        tier=UsageTier.METADATA_ONLY,
        licence="All rights reserved",
        rationale=(
            "Method publishes original copyrighted raid and dungeon guides "
            "with no content licence and no public content API. As with Icy "
            "Veins we retain only title, URL and encounter taxonomy so the "
            "assistant can refer users to the guide, never its prose."
        ),
        attribution="Guide index from Method (method.gg).",
        allows_model_training=False,
        min_request_interval=2.0,
        notes={"verified": "2026-08-02"},
    ),
    SourceKey.PETOPIA: SourcePolicy(
        key=SourceKey.PETOPIA,
        display_name="Petopia",
        homepage="https://www.wow-petopia.com",
        tier=UsageTier.METADATA_ONLY,
        licence="All rights reserved",
        rationale=(
            "Petopia is a long-running community fan site with no robots.txt "
            "(the path 404s), no content licence and no API. Absent an "
            "affirmative grant we take the conservative route: hunter pet "
            "family and tameability facts are sourced from the Blizzard API "
            "instead, and Petopia is retained only as a citable reference "
            "link for appearance/location lore."
        ),
        attribution="Hunter pet reference: Petopia (wow-petopia.com).",
        allows_model_training=False,
        min_request_interval=3.0,
        notes={
            "verified": "2026-08-02",
            "prefer_instead": "blizzard_game_data",
        },
    ),
}

#: Read-only view of the registry. Mutating policy at runtime is not supported.
SOURCE_POLICIES: Mapping[SourceKey, SourcePolicy] = MappingProxyType(_POLICIES)


def get_policy(key: SourceKey | str) -> SourcePolicy:
    """Return the policy for ``key``.

    Raises:
        PolicyViolationError: if the source is not registered. An unregistered
            source has, by definition, no reviewed licence — so it is refused
            rather than defaulted.
    """
    try:
        source_key = SourceKey(key)
    except ValueError as exc:
        raise PolicyViolationError(
            f"Unknown knowledge source {key!r}. Every source must be "
            "registered in app.knowledge.policy with a reviewed usage tier "
            "before it can be ingested."
        ) from exc
    return SOURCE_POLICIES[source_key]


def assert_text_storage_allowed(key: SourceKey | str) -> SourcePolicy:
    """Assert that body text from ``key`` may be persisted and embedded.

    Called by the ingestion pipeline immediately before writing chunks.

    Raises:
        PolicyViolationError: if the source is disabled or is not licensed
            for full-text retention.
    """
    policy = get_policy(key)
    if policy.tier is UsageTier.DISABLED:
        raise PolicyViolationError(
            f"Ingestion from {policy.display_name} is disabled. "
            f"{policy.rationale}"
        )
    if not policy.tier.allows_text_storage:
        raise PolicyViolationError(
            f"{policy.display_name} is registered as '{policy.tier.value}'; "
            "storing its body text is not permitted. Ingest structured "
            "fields or metadata instead."
        )
    return policy
