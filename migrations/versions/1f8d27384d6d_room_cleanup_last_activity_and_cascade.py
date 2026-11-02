"""room_cleanup_last_activity_and_cascade

Revision ID: 1f8d27384d6d
Revises: abd13a9e70ac
Create Date: 2026-10-18 22:57:40.471325

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f8d27384d6d'
down_revision: Union[str, None] = 'abd13a9e70ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # last_activity_at for the idle sweeper
    op.add_column(
        "rooms",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE rooms SET last_activity_at = COALESCE(created_at, NOW())"
    )
    op.create_index(
        "ix_rooms_last_activity_at",
        "rooms",
        ["last_activity_at"],
        postgresql_where=sa.text("is_active = true"),
    )

    # Recreate room_id FK with ON DELETE CASCADE
    op.drop_constraint(
        "room_participants_room_id_fkey",
        "room_participants",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "room_participants_room_id_fkey",
        "room_participants",
        "rooms",
        ["room_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Recreate user_id FK with ON DELETE CASCADE
    op.drop_constraint(
        "room_participants_user_id_fkey",
        "room_participants",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "room_participants_user_id_fkey",
        "room_participants",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_index(
        "ix_room_participants_room_id",
        "room_participants",
        ["room_id"],
        unique=False,
    )
    op.create_index(
        "ix_room_participants_user_id",
        "room_participants",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_room_participants_user_id", table_name="room_participants")
    op.drop_index("ix_room_participants_room_id", table_name="room_participants")

    op.drop_constraint(
        "room_participants_user_id_fkey",
        "room_participants",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "room_participants_user_id_fkey",
        "room_participants",
        "users",
        ["user_id"],
        ["id"],
    )

    op.drop_constraint(
        "room_participants_room_id_fkey",
        "room_participants",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "room_participants_room_id_fkey",
        "room_participants",
        "rooms",
        ["room_id"],
        ["id"],
    )

    op.drop_index("ix_rooms_last_activity_at", table_name="rooms")
    op.drop_column("rooms", "last_activity_at")
