"""Realm and guild lookup against Blizzard's Game Data APIs."""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.integrations.blizzard.client import BlizzardAPIClient
from app.integrations.blizzard.constants import (
    REGIONS,
    Endpoints,
    Namespace,
    guild_slug,
    realm_slug,
)
from app.integrations.blizzard.parsers import (
    parse_guild,
    parse_guild_roster,
    parse_realm_index,
    parse_realm_search,
)

logger = get_logger(__name__)


class BlizzardSearchService:
    """Realm and guild discovery.

    Results are heavily cached: realm lists change a few times a year, and
    guild rosters no more than a few times a day.
    """

    def __init__(self, client: BlizzardAPIClient | None = None) -> None:
        self.client = client or BlizzardAPIClient()

    @staticmethod
    def _validate_region(region: str | None) -> str:
        resolved = (region or settings.BLIZZARD_REGION).lower()
        if resolved not in REGIONS:
            raise ValidationError(
                f"Unsupported region {resolved!r}. "
                f"Choose one of: {', '.join(sorted(REGIONS))}."
            )
        return resolved

    # -- Realms ------------------------------------------------------------

    async def search_realms(
        self,
        query: str | None = None,
        *,
        region: str | None = None,
        limit: int = 25,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """Search realms by name.

        Blizzard's search endpoint matches on the locale-specific name field,
        so we target the configured locale. With no query the full realm
        index is returned instead, which is cheaper and complete.
        """
        resolved_region = self._validate_region(region)
        client = self._client_for(resolved_region)

        if not query or not query.strip():
            payload = await client.get(
                Endpoints.REALM_INDEX,
                namespace=Namespace.DYNAMIC,
                cache_ttl=settings.BLIZZARD_STATIC_CACHE_TTL,
            )
            realms = parse_realm_index(payload or {})
            realms.sort(key=lambda r: (r["name"] or "").lower())
            return realms[:limit]

        locale = settings.BLIZZARD_LOCALE
        payload = await client.get(
            Endpoints.REALM_SEARCH,
            namespace=Namespace.DYNAMIC,
            params={
                f"name.{locale}": query.strip(),
                "orderby": f"name.{locale}",
                "_page": max(1, page),
                "_pageSize": min(max(limit, 1), 100),
            },
            cache_ttl=settings.BLIZZARD_STATIC_CACHE_TTL,
        )
        return parse_realm_search(payload or {})

    async def get_realm(
        self, slug: str, *, region: str | None = None
    ) -> dict[str, Any]:
        """Fetch a single realm by slug."""
        resolved_region = self._validate_region(region)
        client = self._client_for(resolved_region)

        payload = await client.get(
            Endpoints.realm(realm_slug(slug)),
            namespace=Namespace.DYNAMIC,
            cache_ttl=settings.BLIZZARD_STATIC_CACHE_TTL,
        )
        if payload is None:
            raise NotFoundError(f"Realm {slug!r} was not found in {resolved_region}.")

        return {
            "id": payload.get("id"),
            "name": payload.get("name"),
            "slug": payload.get("slug"),
            "category": payload.get("category"),
            "timezone": payload.get("timezone"),
            "locale": payload.get("locale"),
            "is_tournament": bool(payload.get("is_tournament")),
        }

    # -- Guilds ------------------------------------------------------------

    async def get_guild(
        self,
        realm: str,
        name: str,
        *,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a guild by realm and name.

        Blizzard has no guild-name search endpoint, so an exact realm/name
        pair is required. Both are slugged here so callers can pass the
        display names a player would type.
        """
        resolved_region = self._validate_region(region)
        client = self._client_for(resolved_region)

        payload = await client.get(
            Endpoints.guild(realm_slug(realm), guild_slug(name)),
            namespace=Namespace.PROFILE,
            cache_ttl=settings.BLIZZARD_PROFILE_CACHE_TTL,
        )
        if payload is None:
            raise NotFoundError(
                f"Guild {name!r} was not found on realm {realm!r}."
            )
        return parse_guild(payload)

    async def get_guild_roster(
        self,
        realm: str,
        name: str,
        *,
        region: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Fetch a guild roster, paginated in-process.

        Blizzard returns the entire roster in one document, so pagination is
        applied here rather than pushed upstream.
        """
        resolved_region = self._validate_region(region)
        client = self._client_for(resolved_region)

        payload = await client.get(
            Endpoints.guild_roster(realm_slug(realm), guild_slug(name)),
            namespace=Namespace.PROFILE,
            cache_ttl=settings.BLIZZARD_PROFILE_CACHE_TTL,
        )
        if payload is None:
            raise NotFoundError(
                f"Guild {name!r} was not found on realm {realm!r}."
            )

        members = parse_guild_roster(payload)
        return {
            "guild": parse_guild(payload.get("guild") or {}),
            "total": len(members),
            "limit": limit,
            "offset": offset,
            "members": members[offset : offset + limit],
        }

    # -- Internals ---------------------------------------------------------

    def _client_for(self, region: str) -> BlizzardAPIClient:
        """Return a client bound to the requested region.

        Realm and guild data is region-partitioned, so a US-configured server
        still needs to answer EU queries correctly.
        """
        if region == self.client.region:
            return self.client
        return BlizzardAPIClient(region=region)
