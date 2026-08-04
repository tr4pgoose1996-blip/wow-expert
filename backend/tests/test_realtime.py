"""Tests for the realtime overlay module.

The live WebSocket binds to PostgreSQL via SessionFactory, so that path is
exercised with a real DB. Here we test the pure building blocks (frame
serialization, connection manager, event bus) and the personalized HELLO
frame builder against the hermetic SQLite session.
"""

from __future__ import annotations

import asyncio

import pytest

from app.db.models.personalization import PlayerProfile
from app.modules.reasoning.domain import PlayerContext
from app.modules.realtime.domain import (
    ConnectionManager,
    FrameType,
    GameEventBus,
    OverlayFrame,
)
from app.modules.realtime.endpoints import _build_hello


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


# --- Frame serialization ---------------------------------------------------


def test_frame_serializes_with_type_and_seq():
    f = OverlayFrame(FrameType.BOSS_ALERT, {"boss": "X"}, seq=3)
    import json

    parsed = json.loads(f.to_json())
    assert parsed["type"] == "boss_alert"
    assert parsed["seq"] == 3
    assert parsed["boss"] == "X"


# --- Connection manager ----------------------------------------------------


@pytest.mark.asyncio
async def test_connection_manager_tracks_and_sends():
    mgr = ConnectionManager()
    from uuid import uuid4

    uid = uuid4()
    ws = FakeWS()
    await mgr.connect(uid, ws)
    assert mgr.active_count(uid) == 1
    frame = OverlayFrame(FrameType.HELLO, {"x": 1})
    await mgr.send(uid, frame)
    assert len(ws.sent) == 1
    # Unknown user: no send, no error.
    await mgr.send(uuid4(), frame)
    assert len(ws.sent) == 1
    await mgr.disconnect(uid, ws)
    assert mgr.active_count(uid) == 0


# --- Event bus -------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_bus_publishes_to_subscriber():
    bus = GameEventBus()
    from uuid import uuid4

    uid = uuid4()
    q: asyncio.Queue = asyncio.Queue()
    bus.subscribe(uid, q)
    bus.publish(uid, OverlayFrame(FrameType.RARE_ALERT, {"name": "R"}))
    got = await asyncio.wait_for(q.get(), timeout=1)
    assert got.type == FrameType.RARE_ALERT


# --- Hello frame builder (personalized) ------------------------------------


@pytest.mark.asyncio
async def test_build_hello_includes_rotation_when_profile_known(session):
    from uuid import uuid4

    uid = uuid4()
    session.add(
        PlayerProfile(
            user_id=uid,
            favorite_spec="Frost Death Knight",
            favorite_spec_id=251,
            favorite_content=["mythic_plus"],
        )
    )
    await session.commit()
    hello = await _build_hello(uid, session)
    assert hello.type == FrameType.HELLO
    assert hello.payload["rotation"]["spec_id"] == 251
    assert hello.payload["cooldowns"]


@pytest.mark.asyncio
async def test_build_hello_no_rotation_without_spec(session):
    from uuid import uuid4

    uid = uuid4()
    session.add(PlayerProfile(user_id=uid))  # no spec
    await session.commit()
    hello = await _build_hello(uid, session)
    assert hello.payload["rotation"] is None
    assert hello.payload["cooldowns"] == []
