# app/api/v1/endpoints/songs.py
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, or_, select
from app.db.session import get_db
from app.models.song import Song
from app.models.user import User
from app.schemas.song import SongResponse
from app.core.dependencies import get_subscribed_user

router = APIRouter()


def _search_filter(q: str, field: str):
    """Case-insensitive substring match against the requested column(s).

    The LIKE metacharacters are escaped on the way in: without this a title
    containing ``%`` or ``_`` would quietly match far more than what was typed,
    and a lone ``%`` would return the whole catalogue as though it were a hit.
    """
    term = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{term}%"

    def column_like(column):
        return column.like(pattern, escape="\\")

    title = column_like(func.lower(Song.song_name))
    artist = column_like(func.lower(Song.artist))

    if field == "title":
        return title
    if field == "artist":
        return artist
    # "all": searching a name without knowing which field it is in. Cheaper for
    # the user than making them try both chips.
    return or_(title, artist)


@router.get("/list", response_model=list[SongResponse])
async def list_songs(
    q: str | None = Query(
        default=None,
        max_length=100,
        description="Case-insensitive substring match; omitted or blank returns everything",
    ),
    field: Literal["all", "title", "artist"] = Query(
        default="all",
        description="Which column q applies to",
    ),
    # None means "everything", which is what this route always returned before
    # it grew paging. Defaulting to a number here would silently truncate the
    # catalogue for clients that never asked to page — including the party
    # code, which looks a song up by id in this list.
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    # The cloud catalogue is the paid surface. Local files on the device are
    # unaffected — LibraryViewModel reads those through LocalAudioService and
    # never touches this route.
    current_user: User = Depends(get_subscribed_user),
):
    """The catalogue the app browses and searches.

    Filtering happens here rather than in the client so a search reaches tracks
    the app has never paged in — which is the whole point of the change.

    The response stays a bare array so clients written before this route grew
    parameters keep parsing it unchanged. ``total`` is therefore not returned;
    a caller that needs to know whether more rows exist asks for ``limit + 1``
    and checks the batch length.
    """
    stmt = select(Song)
    if q and q.strip():
        stmt = stmt.where(_search_filter(q, field))

    # Stable ordering matters for offset paging: without it two rows sharing a
    # title can swap places between requests and one of them is never seen.
    stmt = stmt.order_by(Song.song_name, Song.artist)
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)

    result = await db.execute(stmt)
    return result.scalars().all()
