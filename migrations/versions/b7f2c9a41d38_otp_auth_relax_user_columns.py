"""relax user columns for OTP auth

Phone number becomes the identity anchor: OTP subscribers arrive with a display
name and nothing else, so email and hashed_password have to be optional, and
username stops being unique because it is now just a label shown in rooms.

Revision ID: b7f2c9a41d38
Revises: ac8ab4c82db5
Create Date: 2026-09-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7f2c9a41d38"
down_revision: Union[str, None] = "ac8ab4c82db5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # OTP subscribers have neither. Postgres treats NULLs as distinct inside a
    # unique index, so ix_users_email keeps working with any number of them and
    # does not need dropping.
    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=True)
    op.alter_column(
        "users", "hashed_password", existing_type=sa.String(length=255), nullable=True
    )

    # Keep the index, drop the uniqueness: a name collision would otherwise fail
    # the insert during /internal/issue-token, i.e. after the OTP was spent.
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=False)

    op.add_column(
        "users",
        sa.Column("subscription_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Only reversible while no OTP-created rows exist — restoring NOT NULL fails
    # if any user has a null email or password, and re-adding the unique index
    # fails if two users share a display name.
    op.drop_column("users", "subscription_checked_at")

    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.alter_column(
        "users", "hashed_password", existing_type=sa.String(length=255), nullable=False
    )
    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=False)
