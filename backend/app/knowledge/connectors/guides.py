"""Guide-index connectors for Icy Veins, Method and Petopia.

These sites publish original copyrighted editorial content under no content
licence. Their robots.txt files permit crawling, but permission to *fetch* is
not permission to *republish* — so the policy registry classes all three as
``METADATA_ONLY`` and :class:`KnowledgeDocument` discards any body text these
connectors produce.

What we therefore build is an **index**, not a copy: title, canonical URL and
taxonomy, so the assistant can tell a player "Icy Veins has a current Frost
Mage guide" and link them to it. Enumeration uses each site's sitemap where
available, which is the mechanism sites publish precisely so crawlers do not
have to spider them.

To ingest full guide text from any of these, obtain written permission and
change the tier in ``app.knowledge.policy`` — not here.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterable
from xml.etree import ElementTree

import httpx

from app.core.logging import get_logger
from app.knowledge.connectors.base import BaseConnector, register_connector
from app.knowledge.documents import EntityType, KnowledgeDocument
from app.knowledge.policy import SourceKey

logger = get_logger(__name__)

__all__ = ["IcyVeinsConnector", "MethodConnector", "PetopiaConnector"]

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

#: URL path fragments mapped to the entity type they represent.
_PATH_ENTITY_HINTS: tuple[tuple[re.Pattern[str], EntityType], ...] = (
    (re.compile(r"/raid|/raids|-raid-"), EntityType.RAID),
    (re.compile(r"/dungeon|mythic-plus|-dungeon-"), EntityType.DUNGEON),
    (re.compile(r"/boss|-boss-|encounter"), EntityType.BOSS),
    (re.compile(r"pve-|pvp-|-guide|talents|rotation"), EntityType.CLASS_SPEC),
    (re.compile(r"profession|herbalism|mining|alchemy"), EntityType.PROFESSION),
    (re.compile(r"mount"), EntityType.MOUNT),
    (re.compile(r"pet|hunter-pets"), EntityType.PET),
    (re.compile(r"achievement"), EntityType.ACHIEVEMENT),
)


def _classify(url: str) -> EntityType:
    """Infer entity type from URL structure."""
    lowered = url.lower()
    for pattern, entity_type in _PATH_ENTITY_HINTS:
        if pattern.search(lowered):
            return entity_type
    return EntityType.GUIDE


def _title_from_url(url: str) -> str:
    """Derive a readable title from a URL slug.

    Deliberately not fetched from the page's ``<title>``: under a
    metadata-only tier we minimise what we retrieve, and the slug is
    sufficient to render a useful link.
    """
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"\.(html?|php)$", "", slug)
    slug = re.sub(r"^\d+-", "", slug)
    words = [w for w in re.split(r"[-_]+", slug) if w]
    if not words:
        return url
    return " ".join(word.capitalize() for word in words)


class _SitemapConnector(BaseConnector):
    """Shared sitemap-walking behaviour for metadata-only guide sites."""

    #: Root sitemap or sitemap index.
    sitemap_url: str
    #: Only URLs containing one of these fragments are indexed.
    url_filters: tuple[str, ...] = ()
    #: Guard against pathological sitemap indexes.
    max_urls: int = 5_000

    async def _fetch_xml(self, url: str) -> ElementTree.Element | None:
        await self._limiter.acquire()
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            return ElementTree.fromstring(response.content)
        except (httpx.HTTPError, ElementTree.ParseError) as exc:
            logger.warning("Could not read sitemap %s: %s", url, exc)
            return None

    async def _walk_sitemap(self, url: str, depth: int = 0) -> AsyncIterator[str]:
        """Yield page URLs, recursing one level into sitemap indexes."""
        root = await self._fetch_xml(url)
        if root is None:
            return

        if root.tag.endswith("sitemapindex"):
            if depth >= 1:
                return
            for node in root.findall("sm:sitemap/sm:loc", _SITEMAP_NS):
                if node.text:
                    async for page in self._walk_sitemap(node.text, depth + 1):
                        yield page
            return

        for node in root.findall("sm:url/sm:loc", _SITEMAP_NS):
            if node.text:
                yield node.text

    async def fetch(self) -> AsyncIterator[KnowledgeDocument]:
        count = 0
        seen: set[str] = set()
        async for url in self._walk_sitemap(self.sitemap_url):
            if count >= self.max_urls:
                logger.info(
                    "%s: reached max_urls=%d", self.policy.display_name,
                    self.max_urls,
                )
                return
            if url in seen:
                continue
            if self.url_filters and not any(f in url for f in self.url_filters):
                continue
            seen.add(url)

            title = _title_from_url(url)
            entity_type = _classify(url)
            try:
                # body is intentionally omitted; the policy tier would strip
                # it regardless. Facts carry the linkable metadata.
                yield KnowledgeDocument(
                    source=self.source,
                    source_id=url,
                    entity_type=entity_type,
                    title=f"{title} \u2014 {self.policy.display_name} guide",
                    url=url,
                    facts={
                        "guide_site": self.policy.display_name,
                        "guide_type": entity_type.value,
                        "note": (
                            "Index entry only. Full guide text is not "
                            "reproduced; follow the link for the guide."
                        ),
                    },
                )
                count += 1
            except ValueError:
                continue


@register_connector
class IcyVeinsConnector(_SitemapConnector):
    """Indexes Icy Veins WoW guide URLs (metadata only)."""

    source = SourceKey.ICY_VEINS
    sitemap_url = "https://www.icy-veins.com/sitemap.xml"
    url_filters = ("/wow/",)


@register_connector
class MethodConnector(_SitemapConnector):
    """Indexes Method WoW guide URLs (metadata only)."""

    source = SourceKey.METHOD
    sitemap_url = "https://www.method.gg/sitemap.xml"
    url_filters = ("/guides", "/wow")


@register_connector
class PetopiaConnector(BaseConnector):
    """Indexes Petopia hunter-pet family reference pages (metadata only).

    Petopia publishes no sitemap and no API, so rather than spider the site we
    index its stable, well-known family pages only. Authoritative pet facts —
    which families exist, what is tameable — come from the Blizzard API
    instead, as the policy's ``prefer_instead`` note records.
    """

    source = SourceKey.PETOPIA

    _BASE = "https://www.wow-petopia.com"

    #: Petopia's family pages follow a stable slug scheme. Kept explicit
    #: rather than crawled, to minimise our footprint on a fan-run site.
    _FAMILIES: tuple[str, ...] = (
        "bat", "bear", "beetle", "bird-of-prey", "boar", "carrion-bird",
        "cat", "chimaera", "clefthoof", "core-hound", "courser", "crab",
        "crane", "crocolisk", "devilsaur", "dog", "dragonhawk", "gorilla",
        "hydra", "hyena", "moth", "nether-ray", "porcupine", "pterrordax",
        "quilen", "raptor", "ravager", "rhino", "riverbeast", "rylak",
        "scalehide", "scorpid", "serpent", "shale-beast", "silithid",
        "spider", "spirit-beast", "sporebat", "stag", "tallstrider",
        "turtle", "warp-stalker", "wasp", "wind-serpent", "wolf", "worm",
    )

    def __init__(self, families: Iterable[str] | None = None, client=None) -> None:
        super().__init__(client=client)
        self._families = tuple(families) if families else self._FAMILIES

    async def fetch(self) -> AsyncIterator[KnowledgeDocument]:
        for family in self._families:
            title = " ".join(word.capitalize() for word in family.split("-"))
            yield KnowledgeDocument(
                source=self.source,
                source_id=family,
                entity_type=EntityType.PET,
                title=f"{title} (hunter pet family) \u2014 Petopia",
                url=f"{self._BASE}/families/{family}/",
                facts={
                    "pet_family": title,
                    "pet_kind": "hunter_pet",
                    "note": (
                        "Reference link only. Appearance and location details "
                        "are on Petopia; tameability and family data come "
                        "from the Blizzard API."
                    ),
                },
            )
