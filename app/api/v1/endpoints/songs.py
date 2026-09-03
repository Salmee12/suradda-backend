# app/api/v1/endpoints/songs.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.models.song import Song
from app.models.user import User
from app.schemas.song import SongResponse
from app.core.dependencies import get_subscribed_user

router = APIRouter()

@router.get("/list", response_model=list[SongResponse])
async def list_songs(
    db: AsyncSession = Depends(get_db),
    # The cloud catalogue is the paid surface. Local files on the device are
    # unaffected — LibraryViewModel reads those through LocalAudioService and
    # never touches this route.
    current_user: User = Depends(get_subscribed_user),
):
    result = await db.execute(select(Song))
    songs = result.scalars().all()
    return songs
