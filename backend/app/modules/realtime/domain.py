"""Realtime overlay domain and connection management.

The desktop overlay connects to a WebSocket and receives a stream of typed
frames: a rotation display, a cooldown tracker, the active quest tracker, boss
and rare-spawn alerts, and waypoint pings. Frames are small, self-describing
JSON so the (transparent) overlay can render them with no extra calls. The
backend also accepts inbound frames (e.g. the overlay reporting the player's
current cast or a manual alert) and re-broadcasts events from the game bus.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID


class FrameType(StrEnum):
    HELLO = "hello"
    ROTATION = "rotation"
    COOLDOWNS = "cooldowns"
    QUESTS = "quests"
    BOSS_ALERT = "boss_alert"
    RARE_ALERT = "rare_alert"
    WAYPOINT = "waypoint"
    ERROR = "error"


@dataclass
class OverlayFrame:
    """A single frame pushed to the overlay."""

    type: FrameType
    payload: dict[str, Any] = field(default_factory=dict)
    # Monotonic sequence so the client can drop out-of-order frames.
    seq: int = 0

    def to_json(self) -> str:
        return json.dumps({"type": self.type.value, "seq": self.seq, **self.payload})


class ConnectionManager:
    """Tracks active overlay websockets per user."""

    def __init__(self) -> None:
        self._conns: dict[UUID, set[Any]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: UUID, ws: Any) -> None:
        async with self._lock:
            self._conns.setdefault(user_id, set()).add(ws)

    async def disconnect(self, user_id: UUID, ws: Any) -> None:
        async with self._lock:
            conns = self._conns.get(user_id)
            if conns:
                conns.discard(ws)
                if not conns:
                    self._conns.pop(user_id, None)

    async def send(self, user_id: UUID, frame: OverlayFrame) -> None:
        conns = self._conns.get(user_id)
        if not conns:
            return
        stale: list[Any] = []
        for ws in list(conns):
            try:
                await ws.send_text(frame.to_json())
            except Exception:
                stale.append(ws)
        for ws in stale:
            conns.discard(ws)

    def active_count(self, user_id: UUID) -> int:
        return len(self._conns.get(user_id, set()))


class GameEventBus:
    """In-process pub/sub for boss/rare alerts and waypoints.

    A real deployment would back this with Redis pub/sub so multiple backend
    workers share events; the interface is identical, so swapping the backing
    is a one-line change.
    """

    def __init__(self) -> None:
        self._subscribers: dict[UUID, list[Any]] = {}

    def subscribe(self, user_id: UUID, queue: Any) -> None:
        self._subscribers.setdefault(user_id, []).append(queue)

    def publish(self, user_id: UUID, frame: OverlayFrame) -> None:
        for q in self._subscribers.get(user_id, []):
            with contextlib.suppress(Exception):
                q.put_nowait(frame)
