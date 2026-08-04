"""Character import and synchronization.

The engine is scope-driven: each importable domain (equipment, talents,
professions, achievements, reputations, mounts, pets) is a
:class:`SyncScope`, fetched concurrently and written independently. A
failure in one scope degrades that scope only — the run is recorded as
``PARTIAL`` and every other domain still lands.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.db.models.blizzard import BlizzardAccount, SyncJob
from app.db.models.character import Character
from app.db.models.enums import (
    CHARACTER_SYNC_SCOPES,
    ContentFocus,
    SyncScope,
    SyncStatus,
    default_role_for_class,
)
from app.integrations.blizzard.client import BlizzardAPIClient
from app.integrations.blizzard.constants import Endpoints, Namespace, realm_slug
from app.integrations.blizzard.parsers import (
    parse_account_profile,
    parse_achievements,
    parse_character_summary,
    parse_equipment,
    parse_mounts,
    parse_pets,
    parse_professions,
    parse_reputations,
    parse_specializations,
)
from app.repositories.blizzard import (
    BlizzardAccountRepository,
    CharacterSnapshotRepository,
    SyncJobRepository,
)
from app.repositories.character import CharacterRepository
from app.services.blizzard_tokens import BlizzardTokenManager
from app.services.character import MAX_CHARACTERS_PER_USER

logger = get_logger(__name__)


class BlizzardSyncService:
    """Imports and refreshes character data from Battle.net."""

    def __init__(
        self,
        session: AsyncSession,
        client: BlizzardAPIClient | None = None,
    ) -> None:
        self.session = session
        self.client = client or BlizzardAPIClient()
        self.accounts = BlizzardAccountRepository(session)
        self.characters = CharacterRepository(session)
        self.snapshots = CharacterSnapshotRepository(session)
        self.jobs = SyncJobRepository(session)
        self.tokens = BlizzardTokenManager(session)

    # -- Roster discovery --------------------------------------------------

    async def fetch_account_roster(
        self, account: BlizzardAccount
    ) -> list[dict[str, Any]]:
        """List every character on the linked Battle.net account."""
        access_token = await self.tokens.get_access_token(account)

        payload = await self.client.get(
            Endpoints.ACCOUNT_PROFILE,
            namespace=Namespace.PROFILE,
            access_token=access_token,
        )
        if payload is None:
            return []
        return parse_account_profile(payload)

    async def import_characters(
        self,
        account: BlizzardAccount,
        *,
        blizzard_ids: list[int] | None = None,
        min_level: int = 1,
    ) -> dict[str, Any]:
        """Import characters from the Battle.net roster into the local DB.

        Args:
            account: The linked Battle.net account.
            blizzard_ids: Restrict the import to these character ids. When
                omitted every roster character at or above ``min_level`` is
                imported.
            min_level: Skip characters below this level; low-level alts
                usually add noise rather than signal.

        Returns a summary of what was imported, skipped, and why.
        """
        roster = await self.fetch_account_roster(account)
        if not roster:
            return {
                "imported": 0, "updated": 0, "skipped": 0,
                "characters": [], "errors": [],
            }

        wanted = set(blizzard_ids) if blizzard_ids else None
        existing_count = await self.characters.count_for_owner(account.user_id)

        candidates = [
            entry
            for entry in roster
            if (wanted is None or entry["blizzard_id"] in wanted)
            and entry["level"] >= min_level
        ]
        skipped = len(roster) - len(candidates)

        imported: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        created = updated = 0

        for index, entry in enumerate(candidates):
            if entry["character_class"] is None:
                # An unrecognised class means Blizzard shipped something new;
                # skip rather than guess, and surface it in the response.
                errors.append(
                    {
                        "character": entry["name"],
                        "error": "Unrecognised character class.",
                    }
                )
                continue

            if existing_count >= MAX_CHARACTERS_PER_USER:
                remaining = len(candidates) - index
                skipped += remaining
                errors.append(
                    {
                        "character": None,
                        "error": (
                            f"Roster limit of {MAX_CHARACTERS_PER_USER} "
                            f"characters reached; {remaining} character(s) "
                            "were not imported."
                        ),
                    }
                )
                break

            try:
                character, was_created = await self._upsert_character(
                    account.user_id, entry, account.region
                )
            except AppError as exc:
                errors.append({"character": entry["name"], "error": exc.message})
                continue

            if was_created:
                created += 1
                existing_count += 1
            else:
                updated += 1

            imported.append(
                {
                    "id": str(character.id),
                    "name": character.name,
                    "realm": character.realm,
                    "level": character.level,
                    "created": was_created,
                }
            )

        await self.accounts.update(
            account, last_sync_at=datetime.now(UTC), sync_error=None
        )

        logger.info(
            "Imported Battle.net roster",
            extra={
                "account_id": str(account.id),
                "created": created,
                "updated": updated,
            },
        )

        return {
            "imported": created,
            "updated": updated,
            "skipped": max(0, skipped),
            "characters": imported,
            "errors": errors,
        }

    async def _upsert_character(
        self, owner_id: uuid.UUID, entry: dict[str, Any], region: str
    ) -> tuple[Character, bool]:
        """Create or refresh a local character from roster data.

        User-authored fields (``goals``, ``content_focus``) are never
        overwritten by an import.
        """
        realm_name = entry["realm"] or entry["realm_slug"] or "unknown"
        existing = await self.characters.get_by_identity(
            owner_id, entry["name"], realm_name
        )

        character_class = entry["character_class"]
        primary_role = default_role_for_class(character_class)

        values: dict[str, Any] = {
            "name": entry["name"],
            "realm": realm_name,
            "region": region,
            "faction": entry["faction"],
            "character_class": character_class,
            "primary_role": primary_role,
            "level": entry["level"],
        }

        if existing is not None:
            external = dict(existing.external_data or {})
            external.update(
                {
                    "blizzard_id": entry["blizzard_id"],
                    "realm_slug": entry["realm_slug"],
                    "race": entry.get("race"),
                }
            )
            updated = await self.characters.update(
                existing, **values, external_data=external
            )
            return updated, False

        character = await self.characters.create(
            owner_id=owner_id,
            content_focus=ContentFocus.LEVELING,
            external_data={
                "blizzard_id": entry["blizzard_id"],
                "realm_slug": entry["realm_slug"],
                "race": entry.get("race"),
            },
            **values,
        )
        return character, True

    # -- Character synchronization -----------------------------------------

    async def sync_character(
        self,
        character: Character,
        *,
        scopes: tuple[SyncScope, ...] = CHARACTER_SYNC_SCOPES,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Fetch and persist the requested scopes for one character.

        All scopes are fetched concurrently. Returns a per-scope report so
        callers can see exactly which domains succeeded.
        """
        slug = (character.external_data or {}).get("realm_slug") or realm_slug(
            character.realm
        )
        name = character.name.lower()

        fetchers = self._build_fetchers(slug, name, scopes, access_token)
        if not fetchers:
            raise ValidationError("No valid synchronization scopes were requested.")

        results = await asyncio.gather(
            *(coro for _, coro in fetchers), return_exceptions=True
        )

        values: dict[str, Any] = {}
        succeeded: list[str] = []
        failed: dict[str, str] = {}

        for (scope, _), result in zip(fetchers, results, strict=True):
            if isinstance(result, BaseException):
                failed[scope.value] = str(result)
                logger.warning(
                    "Sync scope %s failed for character %s: %s",
                    scope.value, character.id, result,
                )
                continue
            if result is None:
                failed[scope.value] = "Blizzard returned no data."
                continue

            values.update(result)
            succeeded.append(scope.value)

        if not succeeded:
            raise NotFoundError(
                f"{character.name} could not be found on Blizzard's servers. "
                "Confirm the name and realm, and that the character is "
                "level 10 or above."
            )

        # Promote a few high-value fields onto the character row itself so
        # listing queries do not need to join the snapshot.
        if "_character_updates" in values:
            await self.characters.update(
                character, **values.pop("_character_updates")
            )

        await self.snapshots.upsert(
            character.id,
            synced_at=datetime.now(UTC),
            synced_scopes=succeeded,
            **values,
        )

        return {
            "character_id": str(character.id),
            "synced": succeeded,
            "failed": failed,
            "status": (
                SyncStatus.SUCCESS.value if not failed else SyncStatus.PARTIAL.value
            ),
        }

    def _build_fetchers(
        self,
        slug: str,
        name: str,
        scopes: tuple[SyncScope, ...],
        access_token: str | None,
    ) -> list[tuple[SyncScope, Any]]:
        """Map each requested scope to its fetch-and-parse coroutine."""
        ttl = settings.BLIZZARD_PROFILE_CACHE_TTL

        async def _fetch(path: str) -> dict[str, Any] | None:
            return await self.client.get(
                path,
                namespace=Namespace.PROFILE,
                cache_ttl=ttl,
                access_token=access_token,
            )

        async def profile() -> dict[str, Any] | None:
            payload = await _fetch(Endpoints.character(slug, name))
            if payload is None:
                return None
            parsed = parse_character_summary(payload)
            updates: dict[str, Any] = {
                "level": parsed["level"] or 1,
                "item_level": parsed["item_level"] or 0,
            }
            if parsed["specialization"]:
                updates["specialization"] = parsed["specialization"][:32]
            if parsed["primary_role"]:
                updates["primary_role"] = parsed["primary_role"]
            return {
                "blizzard_character_id": parsed["blizzard_id"],
                "guild_name": (parsed["guild_name"] or None),
                "race": parsed["race"],
                "achievement_points": parsed["achievement_points"],
                "last_login_at": parsed["last_login"],
                "_character_updates": updates,
            }

        async def equipment() -> dict[str, Any] | None:
            payload = await _fetch(Endpoints.character_equipment(slug, name))
            if payload is None:
                return None
            items = parse_equipment(payload)
            return {"equipment": {"items": items, "count": len(items)}}

        async def talents() -> dict[str, Any] | None:
            payload = await _fetch(
                Endpoints.character_specializations(slug, name)
            )
            return None if payload is None else {
                "talents": parse_specializations(payload)
            }

        async def professions() -> dict[str, Any] | None:
            payload = await _fetch(Endpoints.character_professions(slug, name))
            return None if payload is None else {
                "professions": parse_professions(payload)
            }

        async def achievements() -> dict[str, Any] | None:
            payload = await _fetch(Endpoints.character_achievements(slug, name))
            return None if payload is None else {
                "achievements": parse_achievements(payload)
            }

        async def reputations() -> dict[str, Any] | None:
            payload = await _fetch(Endpoints.character_reputations(slug, name))
            if payload is None:
                return None
            standings = parse_reputations(payload)
            return {
                "reputations": {"total": len(standings), "factions": standings}
            }

        async def mounts() -> dict[str, Any] | None:
            payload = await _fetch(Endpoints.character_mounts(slug, name))
            return None if payload is None else {"mounts": parse_mounts(payload)}

        async def pets() -> dict[str, Any] | None:
            payload = await _fetch(Endpoints.character_pets(slug, name))
            return None if payload is None else {"pets": parse_pets(payload)}

        builders = {
            SyncScope.PROFILE: profile,
            SyncScope.EQUIPMENT: equipment,
            SyncScope.TALENTS: talents,
            SyncScope.PROFESSIONS: professions,
            SyncScope.ACHIEVEMENTS: achievements,
            SyncScope.REPUTATIONS: reputations,
            SyncScope.MOUNTS: mounts,
            SyncScope.PETS: pets,
        }

        expanded = CHARACTER_SYNC_SCOPES if SyncScope.FULL in scopes else scopes
        return [
            (scope, builders[scope]())
            for scope in expanded
            if scope in builders
        ]

    # -- Account-wide collections ------------------------------------------

    async def sync_account_collections(
        self, account: BlizzardAccount
    ) -> dict[str, Any]:
        """Import the account-wide mount and pet collections.

        These differ from the per-character endpoints: they reflect every
        mount and pet the *account* has learned, which is what a player
        actually means by "my collection".
        """
        access_token = await self.tokens.get_access_token(account)

        mounts_payload, pets_payload = await asyncio.gather(
            self.client.get(
                Endpoints.ACCOUNT_MOUNTS,
                namespace=Namespace.PROFILE,
                access_token=access_token,
            ),
            self.client.get(
                Endpoints.ACCOUNT_PETS,
                namespace=Namespace.PROFILE,
                access_token=access_token,
            ),
            return_exceptions=False,
        )

        return {
            "mounts": parse_mounts(mounts_payload or {}),
            "pets": parse_pets(pets_payload or {}),
        }

    # -- Orchestrated jobs -------------------------------------------------

    async def run_account_sync(
        self,
        account: BlizzardAccount,
        *,
        scope: SyncScope = SyncScope.FULL,
        import_new: bool = True,
    ) -> SyncJob:
        """Synchronize every character on an account, recording a job.

        Refuses to start when a job is already running for the account, so a
        user hammering the button cannot multiply upstream load.
        """
        if await self.jobs.get_running(account.id) is not None:
            raise ConflictError(
                "A synchronization is already running for this account."
            )

        job = await self.jobs.create(
            account_id=account.id,
            scope=scope,
            status=SyncStatus.RUNNING,
            started_at=datetime.now(UTC),
        )

        synced = failed = 0
        details: dict[str, Any] = {}

        try:
            if import_new:
                details["import"] = await self.import_characters(account)

            characters = await self.characters.list_for_owner(
                account.user_id, limit=MAX_CHARACTERS_PER_USER, offset=0
            )
            characters = [c for c in characters if c.region == account.region]

            scopes = (
                CHARACTER_SYNC_SCOPES if scope is SyncScope.FULL else (scope,)
            )

            # Process in batches so a large roster does not open hundreds of
            # concurrent upstream connections at once.
            per_character: list[dict[str, Any]] = []
            batch_size = max(1, settings.SYNC_BATCH_SIZE)

            for start in range(0, len(characters), batch_size):
                batch = characters[start : start + batch_size]
                results = await asyncio.gather(
                    *(
                        self.sync_character(character, scopes=scopes)
                        for character in batch
                    ),
                    return_exceptions=True,
                )

                for character, result in zip(batch, results, strict=True):
                    if isinstance(result, BaseException):
                        failed += 1
                        per_character.append(
                            {
                                "character": character.name,
                                "status": "failed",
                                "error": str(result),
                            }
                        )
                    else:
                        synced += 1
                        per_character.append(
                            {"character": character.name, **result}
                        )

            details["characters"] = per_character

            status = (
                SyncStatus.SUCCESS if failed == 0
                else SyncStatus.PARTIAL if synced > 0
                else SyncStatus.FAILED
            )

            job = await self.jobs.update(
                job,
                status=status,
                finished_at=datetime.now(UTC),
                characters_synced=synced,
                characters_failed=failed,
                details=details,
            )
            await self.accounts.update(
                account,
                last_sync_at=datetime.now(UTC),
                sync_error=None,
            )

        except Exception as exc:
            logger.exception("Account sync failed", exc_info=exc)
            job = await self.jobs.update(
                job,
                status=SyncStatus.FAILED,
                finished_at=datetime.now(UTC),
                characters_synced=synced,
                characters_failed=failed,
                error_message=str(exc)[:1000],
                details=details,
            )
            await self.accounts.update(account, sync_error=str(exc)[:500])
            raise

        logger.info(
            "Account sync finished",
            extra={
                "account_id": str(account.id),
                "synced": synced,
                "failed": failed,
                "status": job.status.value,
            },
        )
        return job
