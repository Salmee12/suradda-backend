"""Cloudinary wrapper for the catalogue-management routes.

The credentials have been in ``settings`` since the project started but nothing
used them: the songs table was filled in by hand with URLs. This module is what
finally makes the upload route possible.

Two things to know about the SDK. It is entirely synchronous, so every call here
is pushed onto a worker thread — calling it directly from an async route would
block the event loop for the whole upload. And Cloudinary has no separate
"audio" resource type: audio files are stored as ``resource_type="video"``, and
passing "raw" instead would upload the bytes but give up transcoding and
streaming delivery, so the mismatch is deliberate.
"""

from __future__ import annotations

from typing import Any, BinaryIO

import cloudinary
import cloudinary.uploader
from starlette.concurrency import run_in_threadpool

from app.core.config import settings

# Everything this app owns lives under one prefix, so an accidental purge can
# never reach assets belonging to something else on the same Cloudinary account.
AUDIO_FOLDER = "shuradda/songs"
COVER_FOLDER = "shuradda/covers"

# Cloudinary's own name for the audio bucket. Exported because the delete route
# has to pass the matching type back to destroy() or the call silently no-ops.
AUDIO_RESOURCE_TYPE = "video"
IMAGE_RESOURCE_TYPE = "image"

_configured = False


class CloudinaryNotConfigured(RuntimeError):
    """Raised instead of letting the SDK fail with a vaguer error later."""


def _ensure_configured() -> None:
    """Configure once per process, on first use rather than at import.

    Import-time configuration would make a missing credential break the whole
    app's startup, including the routes that have nothing to do with uploads.
    """
    global _configured
    if _configured:
        return

    missing = [
        name
        for name, value in (
            ("CLOUDINARY_CLOUD_NAME", settings.CLOUDINARY_CLOUD_NAME),
            ("CLOUDINARY_API_KEY", settings.CLOUDINARY_API_KEY),
            ("CLOUDINARY_API_SECRET", settings.CLOUDINARY_API_SECRET),
        )
        if not value
    ]
    if missing:
        raise CloudinaryNotConfigured(
            "Cloudinary is not configured; missing " + ", ".join(missing)
        )

    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )
    _configured = True


def _upload_sync(
    file_obj: BinaryIO, folder: str, resource_type: str, display_name: str | None
) -> dict[str, Any]:
    _ensure_configured()
    return cloudinary.uploader.upload(
        file_obj,
        folder=folder,
        resource_type=resource_type,
        # Let Cloudinary derive the public_id from the filename but keep it
        # collision-proof, so uploading two different takes of the same song
        # does not have the second one overwrite the first.
        use_filename=True,
        unique_filename=True,
        overwrite=False,
        context={"caption": display_name} if display_name else None,
    )


async def upload_audio(file_obj: BinaryIO, display_name: str | None = None) -> tuple[str, str]:
    """Store an audio file. Returns ``(secure_url, public_id)``."""
    result = await run_in_threadpool(
        _upload_sync, file_obj, AUDIO_FOLDER, AUDIO_RESOURCE_TYPE, display_name
    )
    return result["secure_url"], result["public_id"]


async def upload_cover(file_obj: BinaryIO, display_name: str | None = None) -> tuple[str, str]:
    """Store a cover image. Returns ``(secure_url, public_id)``."""
    result = await run_in_threadpool(
        _upload_sync, file_obj, COVER_FOLDER, IMAGE_RESOURCE_TYPE, display_name
    )
    return result["secure_url"], result["public_id"]


def _destroy_sync(public_id: str, resource_type: str) -> dict[str, Any]:
    _ensure_configured()
    return cloudinary.uploader.destroy(
        public_id,
        resource_type=resource_type,
        # Without this the file is gone from the media library but the CDN keeps
        # serving it from the edge for a long while, which looks like the delete
        # did not work.
        invalidate=True,
    )


async def destroy_asset(public_id: str, resource_type: str) -> str:
    """Delete one stored asset. Returns Cloudinary's ``result`` string.

    "ok" on success, "not found" if it was already gone — that second case is
    reported rather than raised, since the caller's goal (the asset no longer
    exists) is satisfied either way.
    """
    result = await run_in_threadpool(_destroy_sync, public_id, resource_type)
    return str(result.get("result", "unknown"))
