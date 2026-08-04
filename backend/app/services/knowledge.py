"""Knowledge engine service layer.

Wires the pipeline components together and exposes the operations the API and
CLI need, so neither has to know about connectors, embedders or stores.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.blizzard.client import BlizzardAPIClient
from app.knowledge.connectors import (
    BlizzardGameDataConnector,
    available_connectors,
    get_connector,
)
from app.knowledge.documents import EntityType
from app.knowledge.embeddings import get_embedding_provider
from app.knowledge.ingestion import IngestionPipeline, IngestionResult
from app.knowledge.policy import (
    SOURCE_POLICIES,
    PolicyViolationError,
    SourceKey,
    UsageTier,
)
from app.knowledge.rag import Answer, RagPipeline
from app.knowledge.vector_store import PgVectorStore, SearchFilters
from app.services.ai_provider import get_ai_provider

logger = get_logger(__name__)

__all__ = ["KnowledgeService"]


def _search_excerpt(chunk, limit: int = 300) -> str:
    """Excerpt a search hit, respecting the source's quotation licence.

    Metadata-only sources are returned as a link and title with no body text,
    matching the tier they were ingested under.
    """
    from app.knowledge.policy import get_policy

    if not get_policy(chunk.source).tier.allows_quotation:
        return ""
    collapsed = " ".join(chunk.text.split())
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed.rfind(" ", 0, limit)
    return collapsed[: cut if cut > 0 else limit].rstrip() + "\u2026"


class KnowledgeService:
    """Application-facing entry point for the knowledge engine."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._store = PgVectorStore(session)
        self._embedder = get_embedding_provider()

    # -- Question answering ----------------------------------------------

    async def ask(
        self,
        question: str,
        *,
        top_k: int = 8,
        sources: list[SourceKey] | None = None,
        entity_types: list[EntityType] | None = None,
        game_version: str | None = None,
        character_context: str | None = None,
    ) -> Answer:
        """Answer a question with citations and a confidence assessment."""
        pipeline = RagPipeline(
            store=self._store,
            embedder=self._embedder,
            ai_provider=get_ai_provider(),
        )

        filters: SearchFilters | None = None
        if sources or entity_types or game_version:
            filters = SearchFilters(
                sources=sources,
                entity_types=entity_types,
                game_version=game_version,
            )

        return await pipeline.answer(
            question,
            top_k=top_k,
            filters=filters,
            character_context=character_context,
        )

    async def search(
        self,
        query: str,
        *,
        limit: int = 20,
        sources: list[SourceKey] | None = None,
        entity_types: list[EntityType] | None = None,
        game_version: str | None = None,
    ) -> list[dict[str, Any]]:
        """Raw retrieval without generation.

        Exposed separately because a lookup ("show me pages about Thunderfury")
        is a different job from a question, and is far cheaper — no model call.
        """
        embedding = await self._embedder.embed_one(query)
        results = await self._store.hybrid_search(
            query,
            embedding,
            limit=limit,
            filters=SearchFilters(
                sources=sources,
                entity_types=entity_types,
                game_version=game_version,
            ),
        )
        return [
            {
                "title": item.chunk.title,
                "url": item.chunk.url,
                "source": item.chunk.source.value,
                "entity_type": item.chunk.entity_type.value,
                "location": item.chunk.display_location,
                "game_version": item.chunk.game_version,
                "score": round(item.score, 4),
                "vector_score": round(item.vector_score, 4),
                "lexical_score": round(item.lexical_score, 4),
                "rank": item.rank,
                "excerpt": _search_excerpt(item.chunk),
            }
            for item in results
        ]

    # -- Ingestion --------------------------------------------------------

    async def ingest_source(
        self,
        source: SourceKey,
        *,
        dry_run: bool = False,
        limit: int | None = None,
        blizzard_client: BlizzardAPIClient | None = None,
    ) -> IngestionResult:
        """Run ingestion for one source.

        Raises:
            PolicyViolationError: if the source is disabled by policy.
        """
        policy = SOURCE_POLICIES[source]
        if policy.tier is UsageTier.DISABLED:
            raise PolicyViolationError(
                f"Ingestion from {policy.display_name} is disabled. "
                f"{policy.rationale}"
            )

        pipeline = IngestionPipeline(self._session, self._embedder)

        if source is SourceKey.BLIZZARD_GAME_DATA:
            if blizzard_client is None:
                raise ValueError(
                    "Blizzard ingestion requires an API client. Pass "
                    "blizzard_client, or configure BLIZZARD_CLIENT_ID and "
                    "BLIZZARD_CLIENT_SECRET."
                )
            connector = BlizzardGameDataConnector(
                blizzard_client, limit_per_type=limit
            )
            try:
                return await pipeline.ingest(
                    source, connector.fetch(), dry_run=dry_run
                )
            finally:
                await connector.close()

        connector_cls = get_connector(source)
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": policy.user_agent},
        ) as client:
            connector = connector_cls(client=client)
            return await pipeline.ingest(
                source, connector.fetch(), dry_run=dry_run
            )

    async def ingest_all(
        self,
        *,
        dry_run: bool = False,
        blizzard_client: BlizzardAPIClient | None = None,
    ) -> list[IngestionResult]:
        """Ingest every enabled source in turn.

        Sources run sequentially rather than concurrently: each has its own
        politeness budget, and running them in parallel would multiply our
        load on community sites for no meaningful wall-clock gain on a job
        that is already scheduled offline.
        """
        results: list[IngestionResult] = []
        for source in available_connectors():
            if source is SourceKey.BLIZZARD_GAME_DATA and blizzard_client is None:
                logger.warning(
                    "Skipping Blizzard ingestion: no API client configured."
                )
                continue
            try:
                results.append(
                    await self.ingest_source(
                        source,
                        dry_run=dry_run,
                        blizzard_client=blizzard_client,
                    )
                )
            except Exception:
                logger.exception("Ingestion failed for %s", source.value)
        return results

    # -- Introspection ----------------------------------------------------

    async def index_stats(self) -> dict[str, Any]:
        """Index contents plus the policy each source operates under."""
        stats = await self._store.stats()
        stats["policies"] = {
            key.value: {
                "display_name": policy.display_name,
                "tier": policy.tier.value,
                "licence": policy.licence,
                "attribution": policy.attribution,
                "enabled": policy.tier is not UsageTier.DISABLED,
                "allows_quotation": policy.tier.allows_quotation,
                "homepage": policy.homepage,
            }
            for key, policy in SOURCE_POLICIES.items()
        }
        stats["embedding_model"] = self._embedder.model_name
        stats["embedding_dimensions"] = self._embedder.dimensions
        return stats
