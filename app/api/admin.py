"""Catalogue management for the cloud song library.

Everything here mutates what every subscriber sees, so it sits behind its own
key rather than behind a user session. That distinction matters: this API has no
notion of an admin account — ``get_subscribed_user`` only proves somebody is
paying, and there are no roles on the users table — so gating writes on a JWT
would let any subscriber empty the library. The gate is ``X-Admin-Key``,
compared in constant time, with the same fail-closed-when-unset behaviour as the
PHP-facing routes in ``app.api.internal``.

Mounted at ``/admin``, outside ``settings.API_V1_PREFIX``: these routes are not
part of the contract the Flutter client is built against and are free to change
without a version bump.

Two behaviours worth reading the code for before relying on them: deleting a
song has to clear ``rooms.current_song_id`` first (see ``delete_song``), and
purging the stored file is opt-in rather than automatic.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Security,
    UploadFile,
    status,
)
from fastapi.security import APIKeyHeader
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models.room import Room
from app.models.song import Song
from app.schemas.song import (
    SongAdminResponse,
    SongCreate,
    SongDeleteResponse,
    SongListResponse,
    SongUpdate,
    clean_hex_code,
)
from app.services import cloudinary_service as cloud

# Imported rather than reimplemented on purpose: this is the one place that
# knows how to read a caller's address through Render's proxy, and two copies of
# that logic would drift.
from app.api.internal import _caller_ips

router = APIRouter()

# auto_error=False so a missing header falls through to the checks below and gets
# the same 401 as a wrong one, instead of FastAPI's own 403 — 403 means
# "subscription lapsed" everywhere else in this API and the client keys off it.
_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)

# Extensions accepted when the browser or curl sends a vague content type.
# Cloudinary is happy with all of these under resource_type="video".
_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac", ".weba"}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}

# Used only when an upload omits hex_code. Picked from the app's own palette so
# an auto-coloured card does not look out of place next to a hand-picked one.
_FALLBACK_PALETTE = (
    "1DB954", "E22134", "3D91F0", "F0A63D",
    "8B5CF6", "16A394", "D9487D", "6B7280",
)


async def require_admin(
    request: Request,
    x_admin_key: str | None = Security(_admin_key_header),
) -> None:
    """Gate for every route in this module."""
    if not settings.ADMIN_API_KEY:
        # Misconfigured rather than unauthorised, and fail closed: a blank key in
        # the environment must never be satisfiable by sending a blank header.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin endpoints are not configured",
        )

    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials"
        )

    allowed = {ip.strip() for ip in settings.ADMIN_ALLOWED_IPS.split(",") if ip.strip()}
    if allowed and not (allowed & _caller_ips(request)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Caller address not allowed"
        )


def _default_hex(song_name: str, artist: str) -> str:
    """A stable colour for an upload that did not specify one.

    Deterministic on purpose — re-uploading the same track gives the same card
    colour, and a random one would make the library look unstable between runs.
    """
    digest = hashlib.md5(f"{song_name}|{artist}".encode()).digest()
    return _FALLBACK_PALETTE[digest[0] % len(_FALLBACK_PALETTE)]


def _extension(filename: str | None) -> str:
    return os.path.splitext(filename or "")[1].lower()


async def _get_song_or_404(song_id: UUID, db: AsyncSession) -> Song:
    song = await db.get(Song, song_id)
    if song is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Song not found")
    return song


@router.get(
    "/songs",
    response_model=SongListResponse,
    dependencies=[Depends(require_admin)],
    summary="List the cloud catalogue",
)
async def list_catalogue(
    q: str | None = Query(default=None, description="Match against title or artist"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """The monitoring view: every row, including the Cloudinary handles.

    ``total`` counts the whole filtered catalogue rather than the returned page,
    so paging through it does not need a second call to know when to stop.
    """
    filters = []
    if q and q.strip():
        # Escape the LIKE metacharacters, otherwise a title containing % or _
        # quietly searches for something broader than what was typed.
        term = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{term}%"
        filters.append(
            or_(
                func.lower(Song.song_name).like(pattern, escape="\\"),
                func.lower(Song.artist).like(pattern, escape="\\"),
            )
        )

    total = await db.scalar(select(func.count()).select_from(Song).where(*filters))
    result = await db.execute(
        select(Song)
        .where(*filters)
        .order_by(Song.song_name, Song.artist)
        .limit(limit)
        .offset(offset)
    )
    return SongListResponse(
        total=total or 0,
        songs=[SongAdminResponse.model_validate(s) for s in result.scalars().all()],
    )


@router.post(
    "/songs",
    response_model=SongAdminResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    summary="Add a track whose files are already hosted",
)
async def create_song(payload: SongCreate, db: AsyncSession = Depends(get_db)):
    """Register a row from URLs you already have.

    This is the route that matches how the catalogue was filled in before, and
    the one to use for anything hosted outside our own Cloudinary account. Set
    the ``*_public_id`` fields only if the files genuinely are ours — the delete
    route will act on them.
    """
    song = Song(**payload.model_dump())
    db.add(song)
    await db.commit()
    await db.refresh(song)
    return song


@router.patch(
    "/songs/{song_id}",
    response_model=SongAdminResponse,
    dependencies=[Depends(require_admin)],
    summary="Edit a track's metadata",
)
async def update_song(
    song_id: str,
    payload: SongUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Partial update — omitted fields keep their current values.

    Pointing ``song_url`` at a different file does not delete the old one; the
    previous asset stays in Cloudinary and its public_id is no longer recorded
    anywhere, so clean that up from the Cloudinary console if it matters.
    """
    song = await _get_song_or_404(song_id, db)

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")

    for field, value in changes.items():
        setattr(song, field, value)

    await db.commit()
    await db.refresh(song)
    return song
@router.post(
    "/songs/upload",
    response_model=SongAdminResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    summary="Add a track by uploading its audio and cover files",
)
async def upload_song(
    song_name: str = Form(..., min_length=1, max_length=100),
    artist: str = Form(..., min_length=1, max_length=255),
    hex_code: str | None = Form(default=None),
    audio: UploadFile = File(...),
    cover: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Register a track by sending Cloudinary the files themselves.

    Use this instead of the URL-based POST /songs when you don't already have
    the files hosted somewhere. hex_code is optional — omit it to get a
    colour derived deterministically from the title and artist. The
    Cloudinary public ids returned are stored on the row so
    DELETE /songs/{song_id} can clean them up later.
    """
    audio_ext = _extension(audio.filename)
    if audio_ext not in _AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported audio file type: {audio_ext or 'unknown'}",
        )

    cover_ext = _extension(cover.filename)
    if cover_ext not in _IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported image file type: {cover_ext or 'unknown'}",
        )

    clean_hex = clean_hex_code(hex_code) if hex_code else _default_hex(song_name, artist)

    try:
        song_url, song_public_id = await cloud.upload_audio(audio.file, display_name=song_name)
        thumbnail_url, thumbnail_public_id = await cloud.upload_cover(cover.file, display_name=song_name)
    except cloud.CloudinaryNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    song = Song(
        song_name=song_name,
        artist=artist,
        song_url=song_url,
        thumbnail_url=thumbnail_url,
        hex_code=clean_hex,
        song_public_id=song_public_id,
        thumbnail_public_id=thumbnail_public_id,
    )
    db.add(song)
    await db.commit()
    await db.refresh(song)
    return song


@router.delete(
    "/songs/{song_id}",
    response_model=SongDeleteResponse,
    dependencies=[Depends(require_admin)],
    summary="Remove a track from the catalogue",
)
async def delete_song(
    song_id: UUID,
    purge_assets: bool = Query(
        default=False, description="Also delete the audio/cover files from Cloudinary"
    ),
    db: AsyncSession = Depends(get_db),
):
    """Delete the catalogue row.

    Any room currently pointing at this track has current_song_id cleared
    first — the foreign key would otherwise block the delete. Cloudinary
    assets are only touched when purge_assets=true is passed, and only for
    whichever of song_public_id / thumbnail_public_id are actually set; rows
    added by hand with someone else's URL are left alone.
    """
    song = await _get_song_or_404(song_id, db)

    result = await db.execute(
        update(Room).where(Room.current_song_id == song_id).values(current_song_id=None)
    )
    rooms_cleared = result.rowcount or 0

    assets_purged: list[str] = []
    asset_errors: list[str] = []

    if purge_assets:
        for public_id, resource_type in (
            (song.song_public_id, cloud.AUDIO_RESOURCE_TYPE),
            (song.thumbnail_public_id, cloud.IMAGE_RESOURCE_TYPE),
        ):
            if not public_id:
                continue
            try:
                outcome = await cloud.destroy_asset(public_id, resource_type)
                if outcome == "ok":
                    assets_purged.append(public_id)
                else:
                    asset_errors.append(f"{public_id}: {outcome}")
            except Exception as exc:  # Cloudinary hiccups shouldn't block the delete
                asset_errors.append(f"{public_id}: {exc}")

    song_name = song.song_name
    await db.delete(song)
    await db.commit()

    return SongDeleteResponse(
        id=song_id,
        song_name=song_name,
        rooms_cleared=rooms_cleared,
        assets_purged=assets_purged,
        asset_errors=asset_errors,
    )



