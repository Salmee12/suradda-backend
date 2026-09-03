import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # A display name, not an identity: it is shown to other participants in a
    # room, users pick it themselves after OTP verification, and two people are
    # allowed to pick the same one. Indexed but deliberately not unique — a
    # collision would otherwise fail the insert *after* the OTP was spent.
    username: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    # Nullable because OTP users never supply one. Postgres treats NULLs as
    # distinct in a unique index, so any number of them coexist.
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    # The identity anchor. Always stored E.164 via app.core.phone.normalize_msisdn.
    phone_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    # Nullable: OTP users have no password. Anything that verifies a password
    # must null-check first — passlib raises on a None hash rather than
    # returning False.
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_subscribed: Mapped[bool] = mapped_column(Boolean, default=False)
    # When BDApps was last asked about this subscription. Lets the refresh cycle
    # re-check on a sane interval instead of on every 30-minute token refresh.
    subscription_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)