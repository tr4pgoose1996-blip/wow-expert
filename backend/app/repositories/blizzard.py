"""Persistence for Battle.net accounts, sync jobs, and snapshots."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models.blizzard import BlizzardAccount, CharacterSnapshot, SyncJob
from app.db.models.enums import SyncStatus
from app.repositories.base import BaseRepository


class BlizzardAccountRepository(BaseRepository[BlizzardAccount]):
    model = BlizzardAccount

    async def get_for_user(
        self, user_id: uuid.UUID, region: str
    ) -> BlizzardAccount | None:
        stmt = select(BlizzardAccount).where(
            BlizzardAccount.user_id == user_id,
            BlizzardAccount.region == region,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[BlizzardAccount]:
        stmt = (
            select(BlizzardAccount)
            .where(BlizzardAccount.user_id == user_id)
            .order_by(BlizzardAccount.created_at.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_battlenet_id(
        self, battlenet_id: int, region: str
    ) -> BlizzardAccount | None:
        stmt = select(BlizzardAccount).where(
            BlizzardAccount.battlenet_id == battlenet_id,
            BlizzardAccount.region == region,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_stale(
        self, *, stale_after_hours: int, limit: int
    ) -> list[BlizzardAccount]:
        """Return active accounts due for a scheduled sync.

        Accounts that have never synced are included, and the oldest are
        returned first so no account is starved by newer arrivals.
        """
        cutoff = datetime.now(UTC) - timedelta(hours=stale_after_hours)
        stmt = (
            select(BlizzardAccount)
            .where(
                BlizzardAccount.is_active.is_(True),
                BlizzardAccount.refresh_token_encrypted.is_not(None),
                (BlizzardAccount.last_sync_at.is_(None))
                | (BlizzardAccount.last_sync_at < cutoff),
            )
            .order_by(BlizzardAccount.last_sync_at.asc().nullsfirst())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())


class SyncJobRepository(BaseRepository[SyncJob]):
    model = SyncJob

    async def list_for_account(
        self, account_id: uuid.UUID, *, limit: int = 20, offset: int = 0
    ) -> list[SyncJob]:
        stmt = (
            select(SyncJob)
            .where(SyncJob.account_id == account_id)
            .order_by(SyncJob.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_running(self, account_id: uuid.UUID) -> SyncJob | None:
        """Return an in-flight job, used to prevent duplicate syncs."""
        stmt = (
            select(SyncJob)
            .where(
                SyncJob.account_id == account_id,
                SyncJob.status.in_([SyncStatus.PENDING, SyncStatus.RUNNING]),
            )
            .order_by(SyncJob.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class CharacterSnapshotRepository(BaseRepository[CharacterSnapshot]):
    model = CharacterSnapshot

    async def get_for_character(
        self, character_id: uuid.UUID
    ) -> CharacterSnapshot | None:
        stmt = select(CharacterSnapshot).where(
            CharacterSnapshot.character_id == character_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self, character_id: uuid.UUID, **values: object
    ) -> CharacterSnapshot:
        """Create the snapshot or update it in place.

        Only the keys supplied are written, so a partial sync (e.g. equipment
        only) leaves previously imported domains intact.
        """
        snapshot = await self.get_for_character(character_id)
        if snapshot is None:
            return await self.create(character_id=character_id, **values)
        return await self.update(snapshot, **values)
