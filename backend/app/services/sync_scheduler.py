"""Background scheduler for weekly Battle.net synchronization.

Runs in-process on a fixed interval, claiming a Redis lock so that only one
replica performs a sweep even when several are deployed. Accounts are picked
up when their last successful sync is older than
``SYNC_STALE_AFTER_HOURS`` (one week by default), which naturally spreads
load rather than stampeding every Tuesday.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from datetime import UTC, datetime

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.session import SessionFactory
from app.integrations.blizzard.client import BlizzardAPIClient
from app.repositories.blizzard import BlizzardAccountRepository
from app.services.blizzard_sync import BlizzardSyncService

logger = get_logger(__name__)

_LOCK_KEY = "sync:weekly:lock"
_WORKER_ID = f"{socket.gethostname()}:{id(object())}"


class SyncScheduler:
    """Periodically synchronizes every stale Battle.net account."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        """Launch the background loop if synchronization is enabled."""
        if not settings.SYNC_ENABLED:
            logger.info("Scheduled synchronization is disabled.")
            return
        if not settings.blizzard_configured:
            logger.info(
                "Blizzard credentials are not configured; "
                "scheduled synchronization will not run."
            )
            return
        if self._task is not None:
            return

        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="blizzard-sync")
        logger.info(
            "Started sync scheduler (interval=%ss, stale after %sh)",
            settings.SYNC_INTERVAL_SECONDS,
            settings.SYNC_STALE_AFTER_HOURS,
        )

    async def stop(self) -> None:
        """Signal the loop to finish and wait briefly for it to unwind."""
        self._stopping.set()
        if self._task is None:
            return

        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("Stopped sync scheduler.")

    async def _run(self) -> None:
        # Stagger startup so simultaneously-deployed replicas do not all
        # contend for the lock in the same instant.
        await self._sleep(5)

        while not self._stopping.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduled sync sweep failed.")

            await self._sleep(settings.SYNC_INTERVAL_SECONDS)

    async def _sleep(self, seconds: float) -> None:
        """Sleep, but wake immediately if shutdown is requested."""
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)

    async def run_once(self) -> dict[str, int]:
        """Perform a single sweep. Returns counts for observability."""
        if not await self._acquire_lock():
            logger.debug("Another replica holds the sync lock; skipping sweep.")
            return {"claimed": 0, "succeeded": 0, "failed": 0}

        succeeded = failed = 0
        client = BlizzardAPIClient()

        try:
            async with SessionFactory() as session:
                accounts = await BlizzardAccountRepository(session).list_stale(
                    stale_after_hours=settings.SYNC_STALE_AFTER_HOURS,
                    limit=settings.SYNC_BATCH_SIZE,
                )

            if not accounts:
                return {"claimed": 0, "succeeded": 0, "failed": 0}

            logger.info("Sync sweep claimed %s account(s)", len(accounts))

            for account in accounts:
                if self._stopping.is_set():
                    break

                # A dedicated session per account keeps one failure from
                # rolling back another account's successful writes.
                async with SessionFactory() as session:
                    try:
                        service = BlizzardSyncService(session, client)
                        reloaded = await service.accounts.get(account.id)
                        if reloaded is None or not reloaded.is_active:
                            continue

                        await service.run_account_sync(reloaded)
                        await session.commit()
                        succeeded += 1
                    except Exception as exc:
                        await session.rollback()
                        failed += 1
                        logger.warning(
                            "Scheduled sync failed for account %s: %s",
                            account.id, exc,
                        )

            return {
                "claimed": len(accounts),
                "succeeded": succeeded,
                "failed": failed,
            }

        finally:
            await client.close()
            await self._release_lock()

    @staticmethod
    async def _acquire_lock() -> bool:
        """Claim the sweep lock.

        The TTL is deliberately longer than a sweep should take, so a worker
        that dies mid-sweep cannot block the next one indefinitely.
        """
        try:
            return bool(
                await get_redis().set(
                    _LOCK_KEY,
                    f"{_WORKER_ID}@{datetime.now(UTC).isoformat()}",
                    nx=True,
                    ex=max(600, settings.SYNC_INTERVAL_SECONDS * 2),
                )
            )
        except RedisError as exc:
            # Without a lock we cannot guarantee single execution, so skip
            # rather than risk every replica syncing the same accounts.
            logger.warning("Could not acquire sync lock: %s", exc)
            return False

    @staticmethod
    async def _release_lock() -> None:
        try:
            await get_redis().delete(_LOCK_KEY)
        except RedisError as exc:
            logger.warning("Could not release sync lock: %s", exc)


scheduler = SyncScheduler()
