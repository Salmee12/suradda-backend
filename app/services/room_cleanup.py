"""Shared room leave / empty-room / host-close cleanup.

Used by:
- POST /rooms/{id}/leave
- WebSocket disconnect
- Background inactivity sweeper

Host leave closes the room (host-authoritative). A non-host leave removes
only that participant; if nobody is left the room is closed too.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.room import Room, RoomParticipant
from app.services.websocket_manager import manager

logger = logging.getLogger(__name__)

# Rooms with no WebSocket activity for this long are closed by the sweeper.
ROOM_IDLE_MINUTES = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def touch_room_activity(db: AsyncSession, room_id: uuid.UUID) -> None:
    """Bump last_activity_at so the sweeper leaves active parties alone."""
    await db.execute(
        update(Room)
        .where(Room.id == room_id, Room.is_active.is_(True))
        .values(last_activity_at=_utcnow())
    )
    await db.commit()


async def _participant_count_db(db: AsyncSession, room_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(RoomParticipant)
        .where(RoomParticipant.room_id == room_id)
    )
    return int(result.scalar_one() or 0)


async def close_room(
    db: AsyncSession,
    room: Room,
    *,
    reason: str = "closed",
) -> None:
    """
    Notify clients, then hard-delete the room row.

    Participants are removed by ON DELETE CASCADE on room_participants.room_id
    (and we also delete them explicitly for clarity if CASCADE is not applied yet).
    Safe to call more than once (second call finds no room).
    """
    room_id = room.id
    payload = {
        "type": "room_closed",
        "room_id": str(room_id),
        "reason": reason,
    }
    try:
        await manager.broadcast(room_id, payload)
    except Exception:
        logger.exception("broadcast room_closed failed for %s", room_id)

    # Explicit participant wipe (works even before CASCADE migration).
    await db.execute(delete(RoomParticipant).where(RoomParticipant.room_id == room_id))
    # Hard-delete the room so it no longer appears in Supabase.
    await db.execute(delete(Room).where(Room.id == room_id))
    await db.commit()

    await manager.disconnect_all(room_id)
    logger.info("Room %s deleted (%s)", room_id, reason)


async def leave_room_cleanup(
    db: AsyncSession,
    room_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict:
    """
    Remove ``user_id`` from the room and close it when appropriate.

    Returns a small status dict for logging / optional response bodies:
      {"left": bool, "closed": bool, "was_host": bool}
    """
    result = await db.execute(select(Room).where(Room.id == room_id))
    room = result.scalar_one_or_none()
    if room is None:
        manager.disconnect(room_id, user_id)
        return {"left": False, "closed": False, "was_host": False}

    was_host = room.host_id == user_id

    # Always drop the socket first so participant_count / broadcasts stay accurate.
    manager.disconnect(room_id, user_id)

    if not room.is_active:
        # Already closed — still scrub a leftover participant row if any.
        await db.execute(
            delete(RoomParticipant).where(
                RoomParticipant.room_id == room_id,
                RoomParticipant.user_id == user_id,
            )
        )
        await db.commit()
        return {"left": True, "closed": False, "was_host": was_host}

    # Host leaves → close the whole party (plan: simpler, host-authoritative).
    if was_host:
        await close_room(db, room, reason="host_left")
        return {"left": True, "closed": True, "was_host": True}

    # Listener leaves → delete their row only.
    await db.execute(
        delete(RoomParticipant).where(
            RoomParticipant.room_id == room_id,
            RoomParticipant.user_id == user_id,
        )
    )
    await db.commit()

    remaining = await _participant_count_db(db, room_id)
    if remaining == 0:
        # Re-load in case of concurrent leaves.
        result = await db.execute(select(Room).where(Room.id == room_id))
        room = result.scalar_one_or_none()
        if room and room.is_active:
            await close_room(db, room, reason="empty")
            return {"left": True, "closed": True, "was_host": False}

    # Room still alive — refresh last activity and tell others who is left.
    await touch_room_activity(db, room_id)
    return {"left": True, "closed": False, "was_host": False}


async def sweep_idle_rooms(db: AsyncSession, idle_minutes: int = ROOM_IDLE_MINUTES) -> int:
    """
    Close active rooms that have had no activity for ``idle_minutes``
    and currently have zero live WebSocket connections.

    Returns how many rooms were closed.
    """
    cutoff = _utcnow() - timedelta(minutes=idle_minutes)
    result = await db.execute(
        select(Room).where(
            Room.is_active.is_(True),
            Room.last_activity_at.is_not(None),
            Room.last_activity_at < cutoff,
        )
    )
    rooms = result.scalars().all()
    closed = 0
    for room in rooms:
        # Someone is still connected over WS → leave it alone.
        if manager.participant_count(room.id) > 0:
            continue
        await close_room(db, room, reason="idle")
        closed += 1
    return closed