import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Six hex digits, with or without the leading hash. Stored without it, because
# that is what the Flutter side already parses out of `hex_code`.
_HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")


def clean_hex_code(value: str) -> str:
    """Normalise a card colour. Public because the upload route takes it as a
    form field and has to apply exactly the same rule."""
    if not _HEX_RE.match(value.strip()):
        raise ValueError("hex_code must be six hex digits, e.g. 1DB954")
    return value.strip().lstrip("#").upper()


def clean_media_url(value: str) -> str:
    url = value.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("must be an http(s) URL")
    return url


class SongResponse(BaseModel):
    """What the app sees. Deliberately does not expose the Cloudinary ids."""

    id: UUID
    song_url: str
    thumbnail_url: str
    artist: str
    song_name: str
    hex_code: str

    model_config = ConfigDict(from_attributes=True)


class SongAdminResponse(SongResponse):
    """Same row plus the storage handles, for the management routes only."""

    song_public_id: str | None = None
    thumbnail_public_id: str | None = None


class SongListResponse(BaseModel):
    """`total` is the count for the whole (filtered) catalogue, not the page."""

    total: int
    songs: list[SongAdminResponse]


class SongCreate(BaseModel):
    """Register a track whose files are already hosted somewhere.

    This is how the catalogue was populated before there was an upload route,
    and it stays useful for anything not stored on our own Cloudinary account.
    Lengths mirror the column widths so an over-long title comes back as a 422
    naming the field rather than a 500 from the database.
    """

    song_name: str = Field(min_length=1, max_length=100)
    artist: str = Field(min_length=1, max_length=255)
    song_url: str
    thumbnail_url: str
    hex_code: str
    # Only set these if the files really are ours to delete; the delete route
    # trusts them when purging.
    song_public_id: str | None = Field(default=None, max_length=255)
    thumbnail_public_id: str | None = Field(default=None, max_length=255)

    _v_hex = field_validator("hex_code")(clean_hex_code)
    _v_urls = field_validator("song_url", "thumbnail_url")(clean_media_url)


class SongUpdate(BaseModel):
    """Partial edit. Every field optional; omitted fields are left as they are.

    None and "absent" have to stay distinguishable here, which is why the two
    public_id fields cannot be cleared through this schema — use the delete
    route if an asset should actually go away.
    """

    song_name: str | None = Field(default=None, min_length=1, max_length=100)
    artist: str | None = Field(default=None, min_length=1, max_length=255)
    song_url: str | None = None
    thumbnail_url: str | None = None
    hex_code: str | None = None

    @field_validator("hex_code")
    @classmethod
    def _hex(cls, value: str | None) -> str | None:
        return None if value is None else clean_hex_code(value)

    @field_validator("song_url", "thumbnail_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return None if value is None else clean_media_url(value)


class SongDeleteResponse(BaseModel):
    """Reports the collateral, because a bare 204 hides too much here.

    `rooms_cleared` is how many live or historical parties were pointing at the
    track — see the delete route for why that has to happen at all.
    `assets_purged` lists the Cloudinary public ids actually destroyed, and
    `asset_errors` anything that refused; the row is gone either way.
    """

    id: UUID
    song_name: str
    rooms_cleared: int
    assets_purged: list[str] = []
    asset_errors: list[str] = []
