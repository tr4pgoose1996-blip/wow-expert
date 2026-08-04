"""Feature module registry.

A *module* is a self-contained vertical slice of wow! expert. — guidance,
and later additions such as raid planning, mentorship matching, or auction
analytics. Each one subclasses :class:`FeatureModule`, declares its router,
and registers itself. ``app.main`` mounts whatever is in the registry, so
adding a system means adding a package and one ``register()`` call — no
edits to the application factory.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from fastapi import APIRouter

from app.core.logging import get_logger

logger = get_logger(__name__)


class FeatureModule(ABC):
    """Contract every pluggable feature module implements."""

    #: Unique machine name, used in the mount path and health report.
    name: str
    #: Human-readable summary surfaced in the OpenAPI tag description.
    description: str = ""
    #: Set False to ship a module dark without removing its code.
    enabled: bool = True

    @property
    @abstractmethod
    def router(self) -> APIRouter:
        """Return the router exposing this module's endpoints."""

    async def startup(self) -> None:  # noqa: B027
        """Optional hook run once during application startup.

        Deliberately concrete and empty: most modules need no setup, and
        forcing every subclass to implement a no-op would be noise.
        """

    async def shutdown(self) -> None:  # noqa: B027
        """Optional hook run once during application shutdown."""

    async def health(self) -> dict[str, str]:
        """Report module health for the readiness endpoint."""
        return {"status": "ok"}


class ModuleRegistry:
    """Ordered collection of feature modules keyed by name."""

    def __init__(self) -> None:
        self._modules: dict[str, FeatureModule] = {}

    def register(self, module: FeatureModule) -> FeatureModule:
        if module.name in self._modules:
            raise ValueError(f"Module {module.name!r} is already registered.")
        self._modules[module.name] = module
        logger.debug("Registered module %s", module.name)
        return module

    def get(self, name: str) -> FeatureModule | None:
        return self._modules.get(name)

    @property
    def all(self) -> list[FeatureModule]:
        return list(self._modules.values())

    @property
    def active(self) -> list[FeatureModule]:
        return [m for m in self._modules.values() if m.enabled]

    def include_in(self, parent: APIRouter, prefix: str = "") -> None:
        """Mount every enabled module onto the parent router."""
        for module in self.active:
            parent.include_router(
                module.router,
                prefix=f"{prefix}/{module.name}",
                tags=[module.name],
            )
            logger.info("Mounted module %s at %s/%s", module.name, prefix, module.name)

    async def startup_all(self) -> None:
        for module in self.active:
            await module.startup()

    async def shutdown_all(self) -> None:
        for module in reversed(self.active):
            try:
                await module.shutdown()
            except Exception:
                logger.exception("Module %s failed to shut down", module.name)

    async def health_all(self) -> dict[str, dict[str, str]]:
        report: dict[str, dict[str, str]] = {}
        for module in self.active:
            try:
                report[module.name] = await module.health()
            except Exception as exc:
                report[module.name] = {"status": "error", "detail": str(exc)}
        return report


registry = ModuleRegistry()
