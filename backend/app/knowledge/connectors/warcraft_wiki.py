"""Warcraft Wiki connector (warcraft.wiki.gg).

Accessed through the MediaWiki Action API rather than by scraping HTML: the
API is the sanctioned machine interface, returns clean wikitext extracts, and
supports continuation for bulk traversal.

Licence: CC BY-SA 4.0, so full text may be retained provided attribution is
carried through — which :class:`KnowledgeDocument` does automatically via the
policy registry. The site signals ``ai-train=no``, so these documents must
never be used as training data; ``allows_model_training`` on the policy
records that constraint for any future training pipeline to honour.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterable

from app.core.logging import get_logger
from app.knowledge.connectors.base import BaseConnector, register_connector
from app.knowledge.documents import EntityType, KnowledgeDocument
from app.knowledge.policy import SourceKey

logger = get_logger(__name__)

__all__ = ["CATEGORY_ENTITY_MAP", "WarcraftWikiConnector"]

_API = "https://warcraft.wiki.gg/api.php"

#: Wiki categories mapped to the entity types they populate. This is the
#: coverage map for "every quest, NPC, zone..." — extend it to widen scope.
CATEGORY_ENTITY_MAP: dict[str, EntityType] = {
    "Quests": EntityType.QUEST,
    "NPCs": EntityType.NPC,
    "Zones": EntityType.ZONE,
    "Items": EntityType.ITEM,
    "Bosses": EntityType.BOSS,
    "Dungeons": EntityType.DUNGEON,
    "Raids": EntityType.RAID,
    "Professions": EntityType.PROFESSION,
    "Achievements": EntityType.ACHIEVEMENT,
    "Mounts": EntityType.MOUNT,
    "Battle pets": EntityType.PET,
    "Factions": EntityType.REPUTATION,
    "Lore": EntityType.LORE,
}

# Wikitext cleanup. Extracts are far cleaner than raw HTML, but templates and
# link syntax still leak through and would otherwise pollute embeddings.
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_FILE_LINK = re.compile(r"\[\[(?:File|Image):[^\]]*\]\]", re.IGNORECASE)
_PIPED_LINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]")
_REF_TAG = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")
_BOLD_ITALIC = re.compile(r"'{2,5}")
_TABLE = re.compile(r"\{\|.*?\|\}", re.DOTALL)


def clean_wikitext(raw: str) -> str:
    """Strip wiki markup down to readable prose.

    Templates are removed iteratively because they nest, and a single pass
    would leave the outer braces of nested templates behind.
    """
    text = _REF_TAG.sub("", raw)
    text = _TABLE.sub("", text)
    text = _FILE_LINK.sub("", text)
    for _ in range(5):
        new_text = _TEMPLATE.sub("", text)
        if new_text == text:
            break
        text = new_text
    text = _PIPED_LINK.sub(r"\1", text)
    text = _HTML_TAG.sub("", text)
    text = _BOLD_ITALIC.sub("", text)
    return text


@register_connector
class WarcraftWikiConnector(BaseConnector):
    """Ingests Warcraft Wiki pages by category."""

    source = SourceKey.WARCRAFT_WIKI

    #: MediaWiki caps list queries at 500 for regular users.
    _PAGE_BATCH = 50
    _CATEGORY_BATCH = 500

    def __init__(
        self,
        categories: Iterable[str] | None = None,
        *,
        max_pages_per_category: int | None = None,
        client=None,
    ) -> None:
        super().__init__(client=client)
        self._categories = list(categories or CATEGORY_ENTITY_MAP)
        self._max_pages = max_pages_per_category

    async def _category_members(self, category: str) -> AsyncIterator[dict]:
        """Yield page stubs in a category, following continuation tokens."""
        params: dict[str, object] = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{category}",
            "cmlimit": self._CATEGORY_BATCH,
            "cmnamespace": 0,  # article namespace only
            "format": "json",
            "formatversion": 2,
        }
        yielded = 0
        while True:
            payload = await self.get_json(_API, params=params)
            members = payload.get("query", {}).get("categorymembers", [])
            for member in members:
                yield member
                yielded += 1
                if self._max_pages and yielded >= self._max_pages:
                    return
            cont = payload.get("continue")
            if not cont:
                return
            params.update(cont)

    async def _fetch_pages(self, page_ids: list[int]) -> list[dict]:
        """Fetch full wikitext extracts for a batch of page ids."""
        payload = await self.get_json(
            _API,
            params={
                "action": "query",
                "pageids": "|".join(str(pid) for pid in page_ids),
                "prop": "extracts|info|revisions",
                "explaintext": 1,
                "exsectionformat": "wiki",
                "inprop": "url",
                "rvprop": "timestamp",
                "format": "json",
                "formatversion": 2,
            },
        )
        return payload.get("query", {}).get("pages", [])

    async def fetch(self) -> AsyncIterator[KnowledgeDocument]:
        """Yield a document for every page in the configured categories."""
        for category in self._categories:
            entity_type = CATEGORY_ENTITY_MAP.get(category, EntityType.OTHER)
            logger.info("Warcraft Wiki: ingesting category %r", category)

            batch: list[int] = []
            async for member in self._category_members(category):
                batch.append(member["pageid"])
                if len(batch) < self._PAGE_BATCH:
                    continue
                async for document in self._emit(batch, entity_type):
                    yield document
                batch = []

            if batch:
                async for document in self._emit(batch, entity_type):
                    yield document

    async def _emit(
        self, page_ids: list[int], entity_type: EntityType
    ) -> AsyncIterator[KnowledgeDocument]:
        """Convert a batch of fetched pages into documents."""
        try:
            pages = await self._fetch_pages(page_ids)
        except Exception:
            # One bad batch must not abort a multi-hour ingestion run.
            logger.exception(
                "Warcraft Wiki: failed to fetch batch of %d pages", len(page_ids)
            )
            return

        for page in pages:
            if page.get("missing") or not page.get("extract"):
                continue

            body = clean_wikitext(page["extract"])
            if len(body) < 80:
                # Stubs and redirects carry no answerable content.
                continue

            timestamp = None
            revisions = page.get("revisions") or []
            if revisions:
                from datetime import datetime

                raw = revisions[0].get("timestamp")
                if raw:
                    timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))

            try:
                yield KnowledgeDocument(
                    source=self.source,
                    source_id=str(page["pageid"]),
                    entity_type=entity_type,
                    title=page["title"],
                    url=page.get(
                        "fullurl",
                        "https://warcraft.wiki.gg/wiki/"
                        + page["title"].replace(" ", "_"),
                    ),
                    body=body,
                    updated_at=timestamp,
                )
            except ValueError:
                logger.warning(
                    "Skipping malformed wiki page %s", page.get("pageid")
                )
