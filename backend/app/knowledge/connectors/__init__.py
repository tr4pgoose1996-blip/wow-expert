"""Knowledge source connectors.

Importing this package registers every available connector. Sources whose
policy tier is DISABLED are documented but not registered — see
``wowhead.py`` for the reasoning behind the one exclusion.
"""

from app.knowledge.connectors.base import (
    BaseConnector,
    available_connectors,
    get_connector,
    register_connector,
)
from app.knowledge.connectors.blizzard_game_data import (
    GAME_DATA_SPECS,
    BlizzardGameDataConnector,
    GameDataSpec,
)
from app.knowledge.connectors.guides import (
    IcyVeinsConnector,
    MethodConnector,
    PetopiaConnector,
)
from app.knowledge.connectors.raider_io import RaiderIOConnector
from app.knowledge.connectors.warcraft_wiki import (
    CATEGORY_ENTITY_MAP,
    WarcraftWikiConnector,
)

__all__ = [
    "CATEGORY_ENTITY_MAP",
    "GAME_DATA_SPECS",
    "BaseConnector",
    "BlizzardGameDataConnector",
    "GameDataSpec",
    "IcyVeinsConnector",
    "MethodConnector",
    "PetopiaConnector",
    "RaiderIOConnector",
    "WarcraftWikiConnector",
    "available_connectors",
    "get_connector",
    "register_connector",
]
