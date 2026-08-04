"""Feature modules.

Importing this package registers every built-in module with the shared
registry. Add new modules here and they are mounted automatically.
"""

from app.modules import (
    coach,  # noqa: F401  (import for side effect)
    collections,  # noqa: F401  (import for side effect)
    gear,  # noqa: F401  (import for side effect)
    guidance,  # noqa: F401  (import for side effect)
    navigation,  # noqa: F401  (import for side effect)
    quests,  # noqa: F401  (import for side effect)
    realtime,  # noqa: F401  (import for side effect)
    reasoning,  # noqa: F401  (import for side effect)
    rotation,  # noqa: F401  (import for side effect)
)
from app.modules.registry import FeatureModule, ModuleRegistry, registry

__all__ = ["FeatureModule", "ModuleRegistry", "registry"]
