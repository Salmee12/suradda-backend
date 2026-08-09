from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SongResponse(BaseModel):
    id: UUID
    song_url: str
    thumbnail_url: str
    artist: str
    song_name: str
    hex_code: str

    model_config = ConfigDict(from_attributes=True)