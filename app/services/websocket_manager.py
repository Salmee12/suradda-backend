from fastapi import WebSocket
from typing import Dict
import uuid
import logging

logger = logging.getLogger(__name__)


class RoomConnectionManager:
    def __init__(self):
        # room_id -> {user_id: WebSocket}
        self.active_connections: Dict[uuid.UUID, Dict[uuid.UUID, WebSocket]] = {}

    async def connect(self, room_id: uuid.UUID, user_id: uuid.UUID, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.setdefault(room_id, {})[user_id] = websocket

    def disconnect(self, room_id: uuid.UUID, user_id: uuid.UUID):
        if room_id in self.active_connections:
            self.active_connections[room_id].pop(user_id, None)
            if not self.active_connections[room_id]:
                del self.active_connections[room_id]

    async def disconnect_all(self, room_id: uuid.UUID):
        """Close every live socket for a room after a room_closed broadcast."""
        connections = self.active_connections.pop(room_id, {})
        for uid, ws in list(connections.items()):
            try:
                await ws.close(code=4000)
            except Exception:
                logger.debug("disconnect_all: socket already dead for %s", uid)

    def participant_count(self, room_id: uuid.UUID) -> int:
        return len(self.active_connections.get(room_id, {}))

    async def broadcast(
        self,
        room_id: uuid.UUID,
        message: dict,
        exclude_user_id: uuid.UUID | None = None,
    ):
        connections = self.active_connections.get(room_id, {})
        dead: list[uuid.UUID] = []
        for uid, ws in list(connections.items()):
            if uid == exclude_user_id:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(uid)
        for uid in dead:
            self.disconnect(room_id, uid)


manager = RoomConnectionManager()