"""YouTube Music → Cloudinary → catalogue import pipeline.

Used only by the admin import routes. Flow per track:

1. ytmusicapi supplies metadata + a thumbnail URL (already filtered for music).
2. yt-dlp downloads the best audio-only stream to a temp file.
3. The existing cloudinary_service uploads the audio (and optionally the cover).
4. A normal ``songs`` row is inserted — same shape as POST /admin/songs/upload.

Nothing here is mounted publicly; the router gates it with ``require_admin``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Any

from collections import Counter
from PIL import Image

import httpx
import yt_dlp
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from ytmusicapi import YTMusic

from app.models.song import Song
from app.services import cloudinary_service as cloud

logger = logging.getLogger(__name__)

# Same palette the upload route uses when hex_code is omitted, so imported
# cards look consistent next to hand-uploaded ones.
_FALLBACK_PALETTE = (
    "1DB954",
    "E22134",
    "3D91F0",
    "F0A63D",
    "8B5CF6",
    "16A394",
    "D9487D",
    "6B7280",
)

# Official-ish music video types returned by YouTube Music. None is allowed
# because many search hits omit the field entirely.
_ALLOWED_VIDEO_TYPES = {
    None,
    "MUSIC_VIDEO_TYPE_ATV",
    "MUSIC_VIDEO_TYPE_OMV",
    "MUSIC_VIDEO_TYPE_OFFICIAL_SOURCE_MUSIC",
}

_BAD_TITLE_KEYWORDS = (
    "podcast",
    "interview",
    "audiobook",
    "episode",
    "spoken word",
    "full album",  # often multi-hour rips; skip for a clean demo catalogue
)

# Lazy singleton — creating YTMusic is cheap but no need to repeat it per request.
_yt: YTMusic | None = None


def _client() -> YTMusic:
    global _yt
    if _yt is None:
        _yt = YTMusic()
    return _yt


def _default_hex(song_name: str, artist: str) -> str:
    digest = hashlib.md5(f"{song_name}|{artist}".encode()).digest()
    return _FALLBACK_PALETTE[digest[0] % len(_FALLBACK_PALETTE)]

def _dominant_hex(image_bytes: bytes) -> str | None:
    """
    Pick a vivid dominant colour from the cover image.
    Skips near-black / near-white / grey pixels so the accent looks good in UI.
    Returns 6-char hex (no #) or None on failure.
    """
    try:
        from io import BytesIO

        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        img = img.resize((50, 50), Image.Resampling.LANCZOS)
        pixels = list(img.getdata())

        candidates = []
        for r, g, b in pixels:
            mx, mn = max(r, g, b), min(r, g, b)
            if mx < 40 or mn > 220:      # too dark / too light
                continue
            if (mx - mn) < 30:           # too grey
                continue
            candidates.append((r, g, b))

        if not candidates:
            candidates = pixels

        (r, g, b), _ = Counter(candidates).most_common(1)[0]
        return f"{r:02X}{g:02X}{b:02X}"
    except Exception as exc:
        logger.warning("dominant_hex failed: %s", exc)
        return None

def _parse_duration_seconds(duration: str | None) -> int | None:
    if not duration:
        return None
    try:
        parts = [int(p) for p in duration.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except (ValueError, AttributeError):
        return None
    return None


def is_likely_music(item: dict[str, Any]) -> bool:
    """Reject obvious non-music hits before we spend a download on them."""
    if not item.get("videoId"):
        return False

    if item.get("videoType") not in _ALLOWED_VIDEO_TYPES:
        return False

    artists = item.get("artists") or []
    if not artists:
        return False

    title = (item.get("title") or "").lower()
    if any(kw in title for kw in _BAD_TITLE_KEYWORDS):
        return False

    seconds = _parse_duration_seconds(item.get("duration"))
    if seconds is not None and (seconds < 60 or seconds > 15 * 60):
        return False

    return True


def _artist_string(artists: list[Any]) -> str:
    names: list[str] = []
    for a in artists:
        if isinstance(a, dict):
            name = a.get("name")
            if name:
                names.append(str(name))
        elif a:
            names.append(str(a))
    return ", ".join(names)[:255] or "Unknown Artist"


def _best_thumbnail(item: dict[str, Any]) -> str:
    """
    Prefer the largest thumbnail by width/height.
    Also try to upgrade common Google user-content URLs to a higher resolution.
    """
    thumbs = item.get("thumbnails") or []
    if not thumbs:
        return ""

    # Pick the entry with the biggest area (more reliable than "last item")
    best = max(
        thumbs,
        key=lambda t: (t.get("width") or 0) * (t.get("height") or 0),
    )
    url = best.get("url") or ""
    if not url:
        return ""

    # Many YT Music covers look like:
    #   ...=w60-h60-l90-rj
    #   ...=w226-h226-l90-rj
    # Bump to a larger square when those size tokens are present.
    import re
    upgraded = re.sub(
        r"=w\d+-h\d+",
        "=w544-h544",   # sharp enough for covers; change to w1200-h1200 if you want max
        url,
        count=1,
    )
    return upgraded or url


def _download_audio_sync(video_id: str, output_dir: Path) -> Path:
    """yt-dlp is synchronous; always call via run_in_threadpool."""
    url = f"https://music.youtube.com/watch?v={video_id}"
    outtmpl = str(output_dir / f"{video_id}.%(ext)s")

    ydl_opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
                "preferredquality": "192",
            }
        ],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    matches = list(output_dir.glob(f"{video_id}.*"))
    if not matches:
        raise FileNotFoundError(f"yt-dlp produced no file for {video_id}")
    return matches[0]


async def _download_thumbnail_bytes(url: str) -> bytes | None:
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content
    except Exception as exc:
        logger.warning("thumbnail download failed (%s): %s", url, exc)
        return None


async def import_track(
    *,
    video_id: str,
    title: str,
    artists: list[Any],
    thumbnail_url: str,
    db: AsyncSession,
    reupload_thumbnail: bool = True,
) -> Song:
    """Download one track, upload to Cloudinary, insert a catalogue row."""
    song_name = (title or "Untitled")[:100]
    artist = _artist_string(artists)

    with tempfile.TemporaryDirectory(prefix="ytm_") as tmp:
        tmp_path = Path(tmp)

        audio_path = await run_in_threadpool(_download_audio_sync, video_id, tmp_path)

        with audio_path.open("rb") as audio_file:
            song_url, song_public_id = await cloud.upload_audio(
                audio_file, display_name=song_name
            )

        # Always try to fetch the cover so we can pick a dominant colour.
        # Re-upload to Cloudinary only when reupload_thumbnail=True.
        thumb_public_id: str | None = None
        final_thumb_url = thumbnail_url
        hex_code = _default_hex(song_name, artist)  # fallback

        raw = await _download_thumbnail_bytes(thumbnail_url) if thumbnail_url else None
        if raw:
            extracted = _dominant_hex(raw)
            if extracted:
                hex_code = extracted

            if reupload_thumbnail:
                cover_path = tmp_path / f"{video_id}_cover.jpg"
                cover_path.write_bytes(raw)
                with cover_path.open("rb") as cover_file:
                    final_thumb_url, thumb_public_id = await cloud.upload_cover(
                        cover_file, display_name=song_name
                    )

        if not final_thumb_url:
            # Catalogue requires a non-null thumbnail_url; use a transparent
            # 1x1 data placeholder only as last resort so the insert never fails.
            final_thumb_url = (
                "https://res.cloudinary.com/demo/image/upload/sample.jpg"
            )

        song = Song(
            song_name=song_name,
            artist=artist,
            song_url=song_url,
            thumbnail_url=final_thumb_url,
            hex_code=hex_code,
            song_public_id=song_public_id,
            thumbnail_public_id=thumb_public_id,
        )
        db.add(song)
        await db.commit()
        await db.refresh(song)
        return song


async def import_from_search(
    *,
    query: str,
    limit: int,
    db: AsyncSession,
    reupload_thumbnail: bool = True,
) -> dict[str, Any]:
    """Search YouTube Music and import up to ``limit`` music tracks."""
    yt = _client()
    # Fetch a few extras so filtering still leaves enough.
    results = await run_in_threadpool(
        lambda: yt.search(query, filter="songs", limit=limit + 8)
    )

    imported: list[Song] = []
    skipped = 0
    errors: list[str] = []

    for item in results:
        if len(imported) >= limit:
            break
        if not is_likely_music(item):
            skipped += 1
            continue

        title = item.get("title") or "Untitled"
        try:
            song = await import_track(
                video_id=item["videoId"],
                title=title,
                artists=item.get("artists") or [],
                thumbnail_url=_best_thumbnail(item),
                db=db,
                reupload_thumbnail=reupload_thumbnail,
            )
            imported.append(song)
            await asyncio.sleep(1.2)  # polite pause between downloads
        except cloud.CloudinaryNotConfigured:
            raise
        except Exception as exc:
            msg = f"{title}: {exc}"
            logger.exception("import failed: %s", msg)
            errors.append(msg)

    return {
        "imported": len(imported),
        "skipped": skipped,
        "errors": errors,
        "songs": imported,
    }


async def import_from_playlist(
    *,
    playlist_id: str,
    limit: int,
    db: AsyncSession,
    reupload_thumbnail: bool = True,
) -> dict[str, Any]:
    """Import up to ``limit`` music tracks from a YouTube Music playlist."""
    yt = _client()
    playlist = await run_in_threadpool(
        lambda: yt.get_playlist(playlist_id, limit=limit + 8)
    )
    tracks = playlist.get("tracks") or []

    imported: list[Song] = []
    skipped = 0
    errors: list[str] = []

    for track in tracks:
        if len(imported) >= limit:
            break
        if not is_likely_music(track):
            skipped += 1
            continue

        title = track.get("title") or "Untitled"
        try:
            song = await import_track(
                video_id=track["videoId"],
                title=title,
                artists=track.get("artists") or [],
                thumbnail_url=_best_thumbnail(track),
                db=db,
                reupload_thumbnail=reupload_thumbnail,
            )
            imported.append(song)
            await asyncio.sleep(1.2)
        except cloud.CloudinaryNotConfigured:
            raise
        except Exception as exc:
            msg = f"{title}: {exc}"
            logger.exception("import failed: %s", msg)
            errors.append(msg)

    return {
        "imported": len(imported),
        "skipped": skipped,
        "errors": errors,
        "songs": imported,
    }
