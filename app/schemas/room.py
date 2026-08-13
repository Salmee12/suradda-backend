import uuid
from pydantic import BaseModel, ConfigDict


class RoomJoin(BaseModel):
    code: str


class RoomState(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    host_id: uuid.UUID
    current_song_id: uuid.UUID | None
    current_position_ms: int
    is_playing: bool
    is_active: bool
    participant_count: int