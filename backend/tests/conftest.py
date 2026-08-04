"""Shared pytest fixtures.

Tests run against SQLite in-memory with a fakeredis-style stub, so the whole
suite is hermetic: no PostgreSQL, no Redis, and no network calls to Blizzard.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

os.environ.setdefault(
    "SECRET_KEY", "test-secret-key-must-be-at-least-32-characters-long"
)
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("SYNC_ENABLED", "false")
os.environ.setdefault("LOG_JSON", "false")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_session
from app.db.models import *  # noqa: F403  (register all models)
from app.db.session import Base
from app.main import create_app


class FakeRedis:
    """Minimal in-memory stand-in for the Redis commands we use."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None,
                  nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def getdel(self, key: str) -> Any:
        return self.store.pop(key, None)

    async def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self.store.pop(k, None) is not None)

    async def exists(self, key: str) -> int:
        return int(key in self.store)

    async def incr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self.store

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.store.clear()

    async def scan_iter(self, match: str = "*", count: int = 100):
        prefix = match.rstrip("*")
        for key in [k for k in self.store if k.startswith(prefix)]:
            yield key

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple]] = []

    def incr(self, key: str) -> FakePipeline:
        self._ops.append(("incr", (key,)))
        return self

    def expire(self, key: str, seconds: int) -> FakePipeline:
        self._ops.append(("expire", (key, seconds)))
        return self

    async def execute(self) -> list[Any]:
        results = []
        for name, args in self._ops:
            results.append(await getattr(self._redis, name)(*args))
        self._ops.clear()
        return results


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Replace the Redis client everywhere it is looked up."""
    instance = FakeRedis()

    import app.api.middleware as middleware
    import app.core.redis as redis_module
    import app.services.blizzard_oauth as oauth_module
    import app.services.sync_scheduler as scheduler_module

    for module in (redis_module, middleware, oauth_module, scheduler_module):
        monkeypatch.setattr(module, "get_redis", lambda: instance, raising=False)

    monkeypatch.setattr(redis_module, "_client", instance, raising=False)
    return instance


@pytest.fixture
async def engine():
    """In-memory SQLite engine shared across a single test."""
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # The knowledge subsystem uses PostgreSQL-only column types (pgvector
    # Vector, raw JSONB with no SQLite variant) that no other dialect can
    # compile. Those tables are covered by their own PostgreSQL-backed tests;
    # here we create everything else so this suite stays hermetic.
    def _is_postgres_only(table) -> bool:
        for column in table.columns:
            type_name = type(column.type).__name__
            if type_name == "Vector":
                return True
            # Our own JSONB columns declare a SQLite variant and compile
            # fine; the knowledge module's raw JSONB columns do not.
            if type_name == "JSONB" and not getattr(
                column.type, "_variant_mapping", None
            ):
                return True
        return False

    tables = [
        table
        for table in Base.metadata.sorted_tables
        if not _is_postgres_only(table)
    ]

    async with test_engine.begin() as connection:
        await connection.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn, tables=tables
            )
        )

    yield test_engine
    await test_engine.dispose()


@pytest.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as db_session:
        yield db_session


@pytest.fixture
async def client(
    engine, fake_redis: FakeRedis
) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client bound to the app with the DB dependency overridden."""
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as db_session:
            try:
                yield db_session
                await db_session.commit()
            except Exception:
                await db_session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _override

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as http_client:
        yield http_client

    app.dependency_overrides.clear()


@pytest.fixture
def user_payload() -> dict[str, str]:
    return {
        "email": "thrall@durotar.org",
        "username": "thrall",
        "password": "ForTheHorde2024",
        "display_name": "Thrall",
    }


@pytest.fixture
async def auth_headers(
    client: AsyncClient, user_payload: dict[str, str]
) -> dict[str, str]:
    """Register a user and return ready-to-use Authorization headers."""
    await client.post("/api/v1/auth/register", json=user_payload)
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "identifier": user_payload["email"],
            "password": user_payload["password"],
        },
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def character_payload() -> dict[str, Any]:
    return {
        "name": "Grommash",
        "realm": "Argent Dawn",
        "region": "eu",
        "faction": "horde",
        "character_class": "warrior",
        "primary_role": "tank",
        "specialization": "Protection",
        "level": 80,
        "item_level": 620,
        "content_focus": "mythic_plus",
        "goals": "Push to +15 keys this season.",
    }
