import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.models.room import Room, RoomParticipant, generate_room_code
from app.models.user import User
from app.schemas.room import RoomJoin, RoomState
from app.core.dependencies import get_current_user, get_subscribed_user
from app.services.websocket_manager import manager

router = APIRouter()


async def _room_state(db: AsyncSession, room: Room) -> RoomState:
    return RoomState(
        id=room.id,
        code=room.code,
        host_id=room.host_id,
        current_song_id=room.current_song_id,
        current_position_ms=room.current_position_ms,
        is_playing=room.is_playing,
        is_active=room.is_active,
        participant_count=manager.participant_count(room.id),
    )


@router.post("/create", response_model=RoomState, status_code=201)
async def create_room(
    db: AsyncSession = Depends(get_db),
    # Starting a listening party is a paid action.
    current_user: User = Depends(get_subscribed_user),
):
    code = generate_room_code()
    # ensure uniqueness (extremely unlikely collision, but check anyway)
    while (await db.execute(select(Room).where(Room.code == code))).scalar_one_or_none():
        code = generate_room_code()

    room = Room(code=code, host_id=current_user.id)
    db.add(room)
    await db.flush()

    participant = RoomParticipant(room_id=room.id, user_id=current_user.id)
    db.add(participant)
    await db.commit()
    await db.refresh(room)

    return await _room_state(db, room)


@router.post("/join", response_model=RoomState)
async def join_room(
    payload: RoomJoin,
    db: AsyncSession = Depends(get_db),
    # So is joining one.
    current_user: User = Depends(get_subscribed_user),
):
    result = await db.execute(select(Room).where(Room.code == payload.code.upper(), Room.is_active == True))
    room = result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Party not found or has ended")

    existing = await db.execute(
        select(RoomParticipant).where(
            RoomParticipant.room_id == room.id, RoomParticipant.user_id == current_user.id
        )
    )
    if not existing.scalar_one_or_none():
        db.add(RoomParticipant(room_id=room.id, user_id=current_user.id))
        await db.commit()

    return await _room_state(db, room)


@router.get("/{room_id}", response_model=RoomState)
async def get_room(
    room_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    # Read-only, and left ungated on purpose: if a subscription lapses while the
    # user is in a room, they still need to be able to see its state to leave.
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Room).where(Room.id == room_id))
    room = result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Party not found")
    return await _room_state(db, room)


@router.post("/{room_id}/leave", status_code=204)
async def leave_room(
    room_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    # Never gated. Blocking a lapsed subscriber from leaving would strand their
    # room open with them listed as host.
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Room).where(Room.id == room_id))
    room = result.scalar_one_or_none()
    if not room:
        return

    if room.host_id == current_user.id:
        room.is_active = False
        await db.commit()
    else:
        participant = await db.execute(
            select(RoomParticipant).where(
                RoomParticipant.room_id == room_id, RoomParticipant.user_id == current_user.id
            )
        )
        p = participant.scalar_one_or_none()
        if p:
            await db.delete(p)
            await db.commit()