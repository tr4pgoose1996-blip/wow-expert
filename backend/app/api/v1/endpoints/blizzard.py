"""Battle.net integration endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Response, status
from fastapi.responses import RedirectResponse

from app.api.dependencies import CurrentUser, Pagination, SessionDep
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.enums import CHARACTER_SYNC_SCOPES, SyncScope
from app.schemas.blizzard import (
    AuthorizeUrlResponse,
    BlizzardAccountRead,
    CharacterSnapshotRead,
    CollectionSummary,
    GuildRead,
    GuildRosterRead,
    ImportRequest,
    ImportResult,
    RealmRead,
    RosterCharacter,
    SyncJobRead,
    SyncRequest,
    SyncResult,
)
from app.schemas.common import Message
from app.services.blizzard_oauth import BlizzardOAuthService
from app.services.blizzard_search import BlizzardSearchService
from app.services.blizzard_sync import BlizzardSyncService
from app.services.character import CharacterService

logger = get_logger(__name__)
router = APIRouter()


# --------------------------------------------------------------------------
# OAuth
# --------------------------------------------------------------------------

@router.get(
    "/oauth/authorize",
    response_model=AuthorizeUrlResponse,
    summary="Begin Battle.net account linking",
    description=(
        "Returns the Battle.net consent URL. Send the user there; Blizzard "
        "redirects back to the configured callback with a single-use code. "
        "The returned link expires after ten minutes."
    ),
)
async def authorize(
    current_user: CurrentUser,
    session: SessionDep,
    region: Annotated[
        str | None, Query(description="Battle.net region (us, eu, kr, tw, cn).")
    ] = None,
) -> AuthorizeUrlResponse:
    url = await BlizzardOAuthService(session).begin(current_user.id, region)
    return AuthorizeUrlResponse(authorize_url=url)


@router.get(
    "/oauth/callback",
    summary="Battle.net OAuth callback",
    description=(
        "Consumes the authorization code from Blizzard and links the "
        "account. This endpoint is called by the user's browser, not by "
        "your application, and redirects back to the frontend."
    ),
    include_in_schema=True,
    responses={
        307: {"description": "Redirect back to the frontend"},
        422: {"description": "Missing, replayed, or expired state"},
    },
)
async def oauth_callback(
    session: SessionDep,
    code: Annotated[str | None, Query(description="Authorization code.")] = None,
    state: Annotated[str | None, Query(description="CSRF state.")] = None,
    error: Annotated[
        str | None, Query(description="Set when the user declined.")
    ] = None,
) -> RedirectResponse:
    frontend = settings.CORS_ORIGINS[0] if settings.CORS_ORIGINS else "/"

    if error:
        logger.info("User declined Battle.net authorization: %s", error)
        return RedirectResponse(
            f"{frontend}/settings/connections?error={error}",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    if not code or not state:
        return RedirectResponse(
            f"{frontend}/settings/connections?error=missing_parameters",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    account = await BlizzardOAuthService(session).complete(code, state)
    return RedirectResponse(
        f"{frontend}/settings/connections?linked={account.battletag or 'ok'}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@router.get(
    "/accounts",
    response_model=list[BlizzardAccountRead],
    summary="List linked Battle.net accounts",
)
async def list_accounts(
    current_user: CurrentUser, session: SessionDep
) -> list[BlizzardAccountRead]:
    accounts = await BlizzardOAuthService(session).list_accounts(current_user.id)
    return [BlizzardAccountRead.model_validate(a) for a in accounts]


@router.delete(
    "/accounts",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect a Battle.net account",
    description="Unlinks the account and permanently deletes its tokens.",
)
async def disconnect_account(
    current_user: CurrentUser,
    session: SessionDep,
    region: Annotated[str | None, Query(description="Region to unlink.")] = None,
) -> Response:
    await BlizzardOAuthService(session).disconnect(current_user.id, region)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------
# Character import and synchronization
# --------------------------------------------------------------------------

@router.get(
    "/roster",
    response_model=list[RosterCharacter],
    summary="Preview the Battle.net character roster",
    description=(
        "Lists every character on the linked account without importing "
        "anything, so the user can choose what to bring in."
    ),
)
async def get_roster(
    current_user: CurrentUser,
    session: SessionDep,
    region: Annotated[str | None, Query()] = None,
) -> list[RosterCharacter]:
    account = await BlizzardOAuthService(session).get_account(
        current_user.id, region
    )
    roster = await BlizzardSyncService(session).fetch_account_roster(account)

    return [
        RosterCharacter(
            blizzard_id=entry["blizzard_id"],
            name=entry["name"],
            realm=entry["realm"],
            realm_slug=entry["realm_slug"],
            level=entry["level"],
            faction=entry["faction"].value if entry["faction"] else None,
            character_class=(
                entry["character_class"].value
                if entry["character_class"] else None
            ),
            race=entry.get("race"),
        )
        for entry in roster
    ]


@router.post(
    "/import",
    response_model=ImportResult,
    summary="Import characters from Battle.net",
    description=(
        "Creates local character profiles from the Battle.net roster. "
        "Re-running updates existing characters in place and never "
        "overwrites user-authored goals or content focus."
    ),
)
async def import_characters(
    payload: ImportRequest,
    current_user: CurrentUser,
    session: SessionDep,
    region: Annotated[str | None, Query()] = None,
) -> ImportResult:
    account = await BlizzardOAuthService(session).get_account(
        current_user.id, region
    )
    result = await BlizzardSyncService(session).import_characters(
        account,
        blizzard_ids=payload.blizzard_ids,
        min_level=payload.min_level,
    )
    return ImportResult(**result)


@router.post(
    "/sync",
    response_model=SyncJobRead,
    summary="Synchronize the whole account",
    description=(
        "Imports any new characters, then refreshes every character's data. "
        "Returns the completed job record. Rejected with 409 when a "
        "synchronization is already running for the account."
    ),
    responses={409: {"description": "A sync is already running"}},
)
async def sync_account(
    current_user: CurrentUser,
    session: SessionDep,
    region: Annotated[str | None, Query()] = None,
    import_new: Annotated[
        bool, Query(description="Also import characters not yet stored.")
    ] = True,
) -> SyncJobRead:
    account = await BlizzardOAuthService(session).get_account(
        current_user.id, region
    )
    job = await BlizzardSyncService(session).run_account_sync(
        account, import_new=import_new
    )
    return SyncJobRead.model_validate(job)


@router.post(
    "/characters/{character_id}/sync",
    response_model=SyncResult,
    summary="Synchronize one character",
    description=(
        "Refreshes the selected data domains for a single character. "
        "Scopes that fail are reported individually; the remainder still "
        "import."
    ),
    responses={404: {"description": "Character not found"}},
)
async def sync_character(
    payload: SyncRequest,
    current_user: CurrentUser,
    session: SessionDep,
    character_id: uuid.UUID = Path(description="Local character identifier."),
) -> SyncResult:
    character = await CharacterService(session).get_owned(
        character_id, current_user.id
    )
    scopes = (
        CHARACTER_SYNC_SCOPES
        if SyncScope.FULL in payload.scopes
        else tuple(payload.scopes)
    )
    result = await BlizzardSyncService(session).sync_character(
        character, scopes=scopes
    )
    return SyncResult(**result)


@router.get(
    "/characters/{character_id}/snapshot",
    response_model=CharacterSnapshotRead,
    summary="Read imported Blizzard data for a character",
    description=(
        "Returns equipment, talents, professions, achievements, "
        "reputations, mounts, and pets as of the last synchronization."
    ),
    responses={404: {"description": "Character or snapshot not found"}},
)
async def get_snapshot(
    current_user: CurrentUser,
    session: SessionDep,
    character_id: uuid.UUID = Path(description="Local character identifier."),
) -> CharacterSnapshotRead:
    from app.core.exceptions import NotFoundError
    from app.repositories.blizzard import CharacterSnapshotRepository

    await CharacterService(session).get_owned(character_id, current_user.id)
    snapshot = await CharacterSnapshotRepository(session).get_for_character(
        character_id
    )
    if snapshot is None:
        raise NotFoundError(
            "This character has not been synchronized yet. "
            "Run a sync to import its data."
        )
    return CharacterSnapshotRead.model_validate(snapshot)


@router.get(
    "/collections",
    response_model=CollectionSummary,
    summary="Read account-wide mount and pet collections",
    description=(
        "Returns every mount and battle pet the Battle.net account has "
        "collected, across all characters."
    ),
)
async def get_collections(
    current_user: CurrentUser,
    session: SessionDep,
    region: Annotated[str | None, Query()] = None,
) -> CollectionSummary:
    account = await BlizzardOAuthService(session).get_account(
        current_user.id, region
    )
    result = await BlizzardSyncService(session).sync_account_collections(account)
    return CollectionSummary(**result)


@router.get(
    "/jobs",
    response_model=list[SyncJobRead],
    summary="List synchronization history",
)
async def list_jobs(
    current_user: CurrentUser,
    session: SessionDep,
    pagination: Pagination,
    region: Annotated[str | None, Query()] = None,
) -> list[SyncJobRead]:
    from app.repositories.blizzard import SyncJobRepository

    account = await BlizzardOAuthService(session).get_account(
        current_user.id, region
    )
    jobs = await SyncJobRepository(session).list_for_account(
        account.id, limit=pagination.limit, offset=pagination.offset
    )
    return [SyncJobRead.model_validate(job) for job in jobs]


# --------------------------------------------------------------------------
# Public game data search
# --------------------------------------------------------------------------

@router.get(
    "/realms",
    response_model=list[RealmRead],
    summary="Search realms",
    description=(
        "Searches realms by name within a region. Omit the query to list "
        "every realm. Results are cached for 24 hours."
    ),
)
async def search_realms(
    query: Annotated[
        str | None, Query(alias="q", max_length=64, description="Realm name.")
    ] = None,
    region: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    page: Annotated[int, Query(ge=1, le=100)] = 1,
) -> list[RealmRead]:
    realms = await BlizzardSearchService().search_realms(
        query, region=region, limit=limit, page=page
    )
    return [RealmRead(**realm) for realm in realms]


@router.get(
    "/realms/{slug}",
    response_model=RealmRead,
    summary="Read a single realm",
    responses={404: {"description": "Realm not found"}},
)
async def get_realm(
    slug: str = Path(description="Realm slug, e.g. 'argent-dawn'."),
    region: Annotated[str | None, Query()] = None,
) -> RealmRead:
    return RealmRead(
        **await BlizzardSearchService().get_realm(slug, region=region)
    )


@router.get(
    "/guilds/{realm}/{name}",
    response_model=GuildRead,
    summary="Look up a guild",
    description=(
        "Blizzard offers no guild-name search, so an exact realm and guild "
        "name are required. Display names are accepted and slugged for you."
    ),
    responses={404: {"description": "Guild not found"}},
)
async def get_guild(
    realm: str = Path(description="Realm name or slug."),
    name: str = Path(description="Guild name or slug."),
    region: Annotated[str | None, Query()] = None,
) -> GuildRead:
    guild = await BlizzardSearchService().get_guild(realm, name, region=region)
    return GuildRead(
        **{**guild, "faction": guild["faction"].value if guild["faction"] else None}
    )


@router.get(
    "/guilds/{realm}/{name}/roster",
    response_model=GuildRosterRead,
    summary="Read a guild roster",
    responses={404: {"description": "Guild not found"}},
)
async def get_guild_roster(
    pagination: Pagination,
    realm: str = Path(description="Realm name or slug."),
    name: str = Path(description="Guild name or slug."),
    region: Annotated[str | None, Query()] = None,
) -> GuildRosterRead:
    result: dict[str, Any] = await BlizzardSearchService().get_guild_roster(
        realm,
        name,
        region=region,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    guild = result["guild"]
    result["guild"] = {
        **guild,
        "faction": guild["faction"].value if guild.get("faction") else None,
    }
    return GuildRosterRead(**result)


@router.get(
    "/status",
    response_model=Message,
    summary="Report integration configuration status",
)
async def integration_status() -> Message:
    if not settings.blizzard_configured:
        return Message(
            detail="Battle.net integration is not configured on this server."
        )
    return Message(
        detail=(
            f"Battle.net integration is active for region "
            f"{settings.BLIZZARD_REGION}."
        )
    )
