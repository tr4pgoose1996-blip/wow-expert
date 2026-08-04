"""Realtime overlay WebSocket endpoint.

Establishes a per-user overlay stream and keeps it fed from the reasoning
engine (rotation/cooldown display + personalized recommendations) and the game
event bus (boss/rare alerts, waypoints). The overlay also pushes state back
(e.g. current cast) which the backend can use to refine future frames.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import SessionFactory
from app.modules.realtime.domain import (
    ConnectionManager,
    FrameType,
    GameEventBus,
    OverlayFrame,
)
from app.modules.reasoning.engine import GameMasterEngine
from app.modules.reasoning.service import _context_from_profile
from app.repositories.personalization import PersonalizationRepository

logger = get_logger(__name__)

router = APIRouter(tags=["realtime"])

# Module-scoped singletons (one per worker process).
manager = ConnectionManager()
event_bus = GameEventBus()

# Demo seed so the overlay is useful even before live game data exists.
_SEED_EVENTS: list[OverlayFrame] = [
    OverlayFrame(
        FrameType.BOSS_ALERT,
        {
            "boss": "Example Mythic Boss",
            "mechanic": "Soak the bombs, then move to the safe sector.",
            "phase": "intermission",
            "severity": "high",
        },
    ),
    OverlayFrame(
        FrameType.RARE_ALERT,
        {"name": "Example Rare", "zone": "Example Zone", "pin": {"x": 0.42, "y": 0.61}},
    ),
    OverlayFrame(
        FrameType.WAYPOINT,
        {
            "label": "Turn-in",
            "pin": {"x": 0.5, "y": 0.5},
            "note": "Hand in weekly quests.",
        },
    ),
]


@router.websocket("/ws/overlay/{user_id}")
async def overlay_ws(websocket: WebSocket, user_id: UUID) -> None:
    await websocket.accept()
    await manager.connect(user_id, websocket)
    queue: asyncio.Queue[OverlayFrame] = asyncio.Queue()
    event_bus.subscribe(user_id, queue)
    seq = 0

    try:
        # Initial personalized frame: rotation/cooldowns + top recommendation.
        async with SessionFactory() as session:
            hello = await _build_hello(user_id, session)
        seq += 1
        hello.seq = seq
        await websocket.send_text(hello.to_json())

        # Stream any seed demo events.
        for ev in _SEED_EVENTS:
            seq += 1
            ev.seq = seq
            await websocket.send_text(ev.to_json())

        # Concurrent producers: outbound event bus + inbound client messages.
        async def _outbound() -> None:
            nonlocal seq
            while True:
                frame = await queue.get()
                seq += 1
                frame.seq = seq
                await websocket.send_text(frame.to_json())

        async def _inbound() -> None:
            while True:
                raw = await websocket.receive_text()
                await _handle_inbound(user_id, raw)

        await asyncio.gather(_outbound(), _inbound())
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(user_id, websocket)
        logger.debug("Overlay ws closed for %s", user_id)


async def _build_hello(user_id: UUID, session: AsyncSession) -> OverlayFrame:
    repo = PersonalizationRepository(session)
    profile = await repo.get_profile(user_id)
    ctx = _context_from_profile(profile)
    payload: dict[str, object] = {"rotation": None, "cooldowns": [], "quests": []}
    if ctx.spec_id is not None:
        engine = GameMasterEngine()
        # Rotation display: static opener + spec so the overlay can drive it.
        payload["rotation"] = {
            "spec_id": ctx.spec_id,
            "spec": ctx.spec,
            "priority_hint": (
                "Open with your highest-priority cooldown, then follow the advisor."
            ),
        }
        # Cooldown tracker seeded from the plan's highest-priority steps.
        plan = engine.plan("what should I do now", ctx)
        payload["cooldowns"] = [
            {"name": o.title, "source": o.source, "priority": o.priority}
            for o in plan.objectives[:4]
        ]
    return OverlayFrame(FrameType.HELLO, payload)


async def _handle_inbound(user_id: UUID, raw: str) -> None:
    """Client -> backend. The overlay can report casts/alerts; we echo them
    back as frames so the design is end-to-end demonstrable."""
    try:
        import json

        msg = json.loads(raw)
        ftype = msg.get("type")
        if ftype in (
            FrameType.BOSS_ALERT.value,
            FrameType.RARE_ALERT.value,
            FrameType.WAYPOINT.value,
        ):
            frame = OverlayFrame(FrameType(ftype), msg.get("payload", {}))
            await manager.send(user_id, frame)
    except Exception:
        return
