import uuid

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class Song(Base):
    __tablename__ = "songs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    song_url: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    thumbnail_url: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    artist: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    song_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    hex_code: Mapped[str] = mapped_column(
        String(6),
        nullable=False,
    )