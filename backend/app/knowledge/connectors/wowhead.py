"""Wowhead — intentionally not implemented.

This module exists to document a deliberate decision rather than an oversight.

``https://www.wowhead.com/robots.txt`` issues ``Disallow: /`` to a block of
AI-related user agents, including ``ClaudeBot``, ``Claude-Web``, ``GPTBot``,
``anthropic-ai``, ``CCBot``, ``cohere-ai``, ``Google-Extended``, ``Bytespider``
and ``Scrapy``. That is an explicit refusal of automated AI ingestion — which
is exactly what this knowledge engine does. Wowhead offers no public content
API and no content licence that would provide an alternative route.

The source is therefore registered in ``app.knowledge.policy`` with
``UsageTier.DISABLED``. Constructing a connector for it raises
``PolicyViolationError``, and ``KnowledgeDocument`` refuses to hold its
content, so the refusal is enforced in code rather than by convention.

**Where Wowhead data would have gone:** almost all of it is available from the
first-party Blizzard Game Data API, which is both authoritative and licensed
for our use. ``BlizzardGameDataConnector`` covers items, quests, achievements,
mounts, pets, professions, journal instances and encounters.

**If you obtain permission:** contact Wowhead/ZAM for written authorisation,
then change the tier in ``app.knowledge.policy`` and implement a connector
here against whatever interface they grant. Do not attempt to enable it by
editing this file alone — the policy registry is the single authority, and it
will still refuse.
"""

from __future__ import annotations

__all__: list[str] = []
