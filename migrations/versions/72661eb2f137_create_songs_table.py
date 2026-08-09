"""create songs table

Revision ID: 72661eb2f137
Revises: ad4359bf16e3
Create Date: 2026-08-07 22:49:11.669012

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.

revision: str = "72661eb2f137"
down_revision: Union[str, None] = "ad4359bf16e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "songs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "song_url",
            sa.String(),
            nullable=False,
        ),
        sa.Column(
            "thumbnail_url",
            sa.String(),
            nullable=False,
        ),
        sa.Column(
            "artist",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "song_name",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "hex_code",
            sa.String(length=6),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("songs")