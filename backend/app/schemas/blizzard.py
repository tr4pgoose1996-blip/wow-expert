"""Battle.net integration schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.db.models.enums import SyncScope, SyncStatus
from app.schemas.common import ORMModel


class BlizzardAccountRead(ORMModel):
    """A linked Battle.net account. Never exposes token material."""

    id: uuid.UUID
    battlenet_id: int
    battletag: str | None
    region: str
    is_active: bool
    scopes: str
    last_sync_at: datetime | None
    sync_error: str | None
    created_at: datetime


class AuthorizeUrlResponse(BaseModel):
    authorize_url: str = Field(
        description="Redirect the user here to grant Battle.net access."
    )
    expires_in: int = Field(
        default=600, description="Seconds before the authorization state expires."
    )


class RosterCharacter(BaseModel):
    """One character on the Battle.net account roster."""

    blizzard_id: int | None = None
    name: str | None = None
    realm: str | None = None
    realm_slug: str | None = None
    level: int = 1
    faction: str | None = None
    character_class: str | None = None
    race: str | None = None


class ImportRequest(BaseModel):
    blizzard_ids: list[int] | None = Field(
        default=None,
        description=(
            "Restrict the import to these Blizzard character ids. "
            "Omit to import the whole roster."
        ),
    )
    min_level: int = Field(
        default=10,
        ge=1,
        le=80,
        description="Skip characters below this level.",
    )


class ImportResult(BaseModel):
    imported: int
    updated: int
    skipped: int
    characters: list[dict[str, Any]]
    errors: list[dict[str, Any]]


class SyncRequest(BaseModel):
    scopes: list[SyncScope] = Field(
        default_factory=lambda: [SyncScope.FULL],
        description="Which data domains to synchronize.",
    )


class SyncResult(BaseModel):
    character_id: str
    synced: list[str] = Field(description="Scopes imported successfully.")
    failed: dict[str, str] = Field(
        default_factory=dict,
        description="Scopes that failed, mapped to the reason.",
    )
    status: str


class SyncJobRead(ORMModel):
    id: uuid.UUID
    account_id: uuid.UUID
    scope: SyncScope
    status: SyncStatus
    started_at: datetime | None
    finished_at: datetime | None
    characters_synced: int
    characters_failed: int
    error_message: str | None
    created_at: datetime


class CharacterSnapshotRead(ORMModel):
    """Imported Blizzard data for a character."""

    character_id: uuid.UUID
    blizzard_character_id: int | None
    guild_name: str | None
    race: str | None
    achievement_points: int
    last_login_at: datetime | None
    equipment: dict[str, Any]
    talents: dict[str, Any]
    professions: dict[str, Any]
    achievements: dict[str, Any]
    reputations: dict[str, Any]
    mounts: dict[str, Any]
    pets: dict[str, Any]
    synced_at: datetime | None
    synced_scopes: list[str]


class RealmRead(BaseModel):
    id: int | None = None
    name: str | None = None
    slug: str | None = None
    region: str | None = None
    category: str | None = None
    timezone: str | None = None
    type: str | None = None
    is_tournament: bool = False
    locale: str | None = None


class GuildRead(BaseModel):
    id: int | None = None
    name: str | None = None
    faction: str | None = None
    realm: str | None = None
    realm_slug: str | None = None
    member_count: int = 0
    achievement_points: int = 0
    created: datetime | None = None


class GuildMember(BaseModel):
    name: str | None = None
    id: int | None = None
    level: int | None = None
    rank: int | None = None
    realm_slug: str | None = None
    character_class: str | None = None
    race: str | None = None


class GuildRosterRead(BaseModel):
    guild: GuildRead
    total: int
    limit: int
    offset: int
    members: list[GuildMember]


class CollectionSummary(BaseModel):
    """Account-wide mount and pet collections."""

    mounts: dict[str, Any]
    pets: dict[str, Any]
