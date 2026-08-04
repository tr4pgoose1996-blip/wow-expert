"""Knowledge engine endpoints: ask, search, and index administration."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import CurrentAdmin, CurrentUser, SessionDep
from app.core.logging import get_logger
from app.knowledge.policy import PolicyViolationError, SourceKey
from app.schemas.knowledge import (
    AskRequest,
    AskResponse,
    IndexStatsResponse,
    IngestRequest,
    IngestResponse,
    SearchRequest,
    SearchResponse,
)
from app.services.character import CharacterService
from app.services.knowledge import KnowledgeService

logger = get_logger(__name__)

router = APIRouter()


async def _character_context(
    session, user_id: uuid.UUID, character_id: str
) -> str | None:
    """Render a character summary for tailoring advice.

    Ownership is verified through the character service, so one user cannot
    use another's character as context.
    """
    try:
        parsed = uuid.UUID(character_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="character_id must be a UUID.",
        ) from None

    character = await CharacterService(session).get_owned(parsed, user_id)
    if character is None:
        return None

    parts = [
        f"Name: {character.name}",
        f"Realm: {character.realm}",
        f"Class: {character.character_class.value}",
        f"Level: {character.level}",
    ]
    if character.faction:
        parts.append(f"Faction: {character.faction.value}")
    if character.role:
        parts.append(f"Role: {character.role.value}")
    if character.item_level:
        parts.append(f"Item level: {character.item_level}")
    return "; ".join(parts)


@router.post(
    "/ask",
    response_model=AskResponse,
    summary="Ask the Game Master a question",
    description=(
        "Answers from the indexed knowledge base using retrieval-augmented "
        "generation. Every answer carries citations and a confidence "
        "assessment.\n\n"
        "When retrieval finds insufficient evidence the engine **refuses to "
        "answer** rather than guessing: check the `refused` flag and the "
        "`confidence.level` field before presenting an answer as reliable."
    ),
    responses={
        422: {"description": "Invalid question or character reference"},
        502: {"description": "The AI or embedding provider is unavailable"},
    },
)
async def ask(
    payload: AskRequest, current_user: CurrentUser, session: SessionDep
) -> AskResponse:
    context = None
    if payload.character_id:
        context = await _character_context(
            session, current_user.id, payload.character_id
        )

    answer = await KnowledgeService(session).ask(
        payload.question,
        top_k=payload.top_k,
        sources=payload.sources,
        entity_types=payload.entity_types,
        game_version=payload.game_version,
        character_context=context,
    )
    return AskResponse.model_validate(answer.as_dict())


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Search the knowledge index",
    description=(
        "Hybrid dense + lexical retrieval with no generation step. Faster and "
        "cheaper than `/ask` when you want sources rather than an answer.\n\n"
        "Excerpts are omitted for sources whose licence does not permit "
        "quotation; follow the URL for those."
    ),
)
async def search(
    payload: SearchRequest, _: CurrentUser, session: SessionDep
) -> SearchResponse:
    results = await KnowledgeService(session).search(
        payload.query,
        limit=payload.limit,
        sources=payload.sources,
        entity_types=payload.entity_types,
        game_version=payload.game_version,
    )
    return SearchResponse(
        query=payload.query, total=len(results), results=results
    )


@router.get(
    "/stats",
    response_model=IndexStatsResponse,
    summary="Index statistics and source policies",
    description=(
        "Reports what is indexed per source, and the licence tier each "
        "source is operated under."
    ),
)
async def stats(_: CurrentUser, session: SessionDep) -> IndexStatsResponse:
    return IndexStatsResponse.model_validate(
        await KnowledgeService(session).index_stats()
    )


@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run ingestion for a source (admin)",
    description=(
        "Fetches, chunks, embeds and indexes one source. Unchanged documents "
        "are skipped by content hash, so re-running is inexpensive.\n\n"
        "Returns 403 for sources disabled by licence policy."
    ),
    responses={
        403: {"description": "Source is disabled by licence policy"},
        502: {"description": "The upstream source is unavailable"},
    },
)
async def ingest(
    payload: IngestRequest, _: CurrentAdmin, session: SessionDep
) -> IngestResponse:
    from app.integrations.blizzard.client import BlizzardAPIClient

    blizzard_client = None
    if payload.source is SourceKey.BLIZZARD_GAME_DATA:
        blizzard_client = BlizzardAPIClient()

    try:
        result = await KnowledgeService(session).ingest_source(
            payload.source,
            dry_run=payload.dry_run,
            limit=payload.limit,
            blizzard_client=blizzard_client,
        )
    except PolicyViolationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    finally:
        if blizzard_client is not None:
            await blizzard_client.close()

    return IngestResponse(
        source=result.source.value,
        tier=result.tier.value,
        status=result.status,
        documents_seen=result.documents_seen,
        documents_written=result.documents_written,
        documents_skipped=result.documents_skipped,
        chunks_written=result.chunks_written,
        embeddings_created=result.embeddings_created,
        duration_seconds=round(result.duration_seconds, 2),
        errors=result.errors[:20],
    )
