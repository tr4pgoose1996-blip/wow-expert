"""ORM model registry.

Importing every model here guarantees they are attached to ``Base.metadata``
before Alembic autogenerate or ``create_all`` inspects it.
"""

from app.db.models.blizzard import BlizzardAccount, CharacterSnapshot, SyncJob
from app.db.models.character import Character
from app.db.models.enums import (
    CHARACTER_SYNC_SCOPES,
    CLASS_ROLES,
    MAX_CHARACTER_LEVEL,
    CharacterClass,
    ContentFocus,
    Faction,
    Role,
    SyncScope,
    SyncStatus,
    UserRole,
    role_is_valid_for_class,
)
from app.db.models.knowledge import (
    IngestionRun,
    KnowledgeChunkRow,
    KnowledgeDocumentRow,
)
from app.db.models.personalization import (
    Conversation,
    ConversationMessage,
    PlayerProfile,
)
from app.db.models.user import User

__all__ = [
    "CHARACTER_SYNC_SCOPES",
    "CLASS_ROLES",
    "MAX_CHARACTER_LEVEL",
    "BlizzardAccount",
    "Character",
    "CharacterClass",
    "CharacterSnapshot",
    "ContentFocus",
    "Conversation",
    "ConversationMessage",
    "Faction",
    "IngestionRun",
    "KnowledgeChunkRow",
    "KnowledgeDocumentRow",
    "PlayerProfile",
    "Role",
    "SyncJob",
    "SyncScope",
    "SyncStatus",
    "User",
    "UserRole",
    "role_is_valid_for_class",
]
