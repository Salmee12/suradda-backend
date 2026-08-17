import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select, update
from app.db.session import AsyncSessionLocal
from app.models.room import Room
from app.models.user import User
from app.core.security import decode_token
from app.services.websocket_manager import manager
from jose import JWTError

router = APIRouter()


async def _broadcast_participants(room_id: uuid.UUID):
    user_ids = list(manager.active_connections.get(room_id, {}).keys())
    if not user_ids:
        return
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        users = result.scalars().all()

    payload = {
        "type": "participants",
        "participants": [{"user_id": str(u.id), "username": u.username} for u in users],
        "count": len(users),
    }
    await manager.broadcast(room_id, payload)


@router.websocket("/rooms/{room_id}")
async def room_websocket(websocket: WebSocket, room_id: uuid.UUID, token: str = Query(...)):
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            await websocket.close(code=4401)
            return
        user_id = uuid.UUID(payload.get("sub"))
    except (JWTError, ValueError):
        await websocket.close(code=4401)
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Room).where(Room.id == room_id))
        room = result.scalar_one_or_none()
        if not room:
            await websocket.close(code=4404)
            return
        is_host = room.host_id == user_id

    await manager.connect(room_id, user_id, websocket)
    await _broadcast_participants(room_id)  # NEW — everyone gets the fresh list, including the new joiner

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type in {"play", "pause", "seek", "sync_position"} and is_host:
                async with AsyncSessionLocal() as db:
                    values = {}
                    if "song_id" in data:
                        values["current_song_id"] = data["song_id"]
                    if "position_ms" in data:
                        values["current_position_ms"] = data["position_ms"]
                    if msg_type == "play":
                        values["is_playing"] = True
                    elif msg_type == "pause":
                        values["is_playing"] = False

                    if values:
                        await db.execute(update(Room).where(Room.id == room_id).values(**values))
                        await db.commit()

                await manager.broadcast(room_id, data, exclude_user_id=user_id)

    except WebSocketDisconnect:
        manager.disconnect(room_id, user_id)
        await _broadcast_participants(room_id)  # NEW — replaces the old count-only broadcast