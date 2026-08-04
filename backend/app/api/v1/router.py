"""Version 1 API router.

Static endpoint groups are declared here; pluggable feature modules are
mounted from the registry, so new systems require no change to this file.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import auth, blizzard, characters, health, knowledge
from app.modules import registry

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(
    characters.router, prefix="/characters", tags=["characters"]
)
api_router.include_router(
    blizzard.router, prefix="/blizzard", tags=["blizzard"]
)
api_router.include_router(
    knowledge.router, prefix="/knowledge", tags=["knowledge"]
)

# Feature modules mount under /modules/<name>.
registry.include_in(api_router, prefix="/modules")

__all__ = ["api_router", "health"]
