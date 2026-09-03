from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta, timezone
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserLogin, UserOut, TokenPair, RefreshRequest
from app.core.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token,
)
from app.core.config import settings
from app.core.dependencies import get_current_user
from app.core.subscription import fetch_subscription_status
from jose import JWTError

router = APIRouter()

@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    # Username is no longer part of the check: it is a display name and is not
    # unique in the DB either, so rejecting a registration over a name clash
    # would be enforcing a constraint that no longer exists.
    existing = await db.execute(
        select(User).where(
            (User.email == payload.email)
            | (User.phone_number == payload.phone_number)
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="Email or phone number already registered")

    user = User(
        username=payload.username,
        email=payload.email,
        phone_number=payload.phone_number,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@router.post("/login", response_model=TokenPair)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)):
    # .first(), not .scalar_one_or_none(): usernames are display names now and
    # two users may share one, which would make scalar_one_or_none raise.
    result = await db.execute(select(User).where(User.username == payload.username))
    user = result.scalars().first()
    # The hashed_password null-check is load-bearing: OTP users have no
    # password, and passlib raises on a None hash instead of returning False —
    # without it this route 500s for them. Same 401 either way, so the response
    # doesn't reveal which accounts are OTP-only.
    if (
        not user
        or not user.hashed_password
        or not verify_password(payload.password, user.hashed_password)
    ):
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    return TokenPair(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
    )

def _recheck_is_due(checked_at: datetime | None) -> bool:
    """Whether is_subscribed is stale enough to be worth a telco round trip."""
    if checked_at is None:
        return True
    # Rows written before subscription_checked_at existed, or by any code path
    # that stored a naive timestamp, would otherwise raise on the comparison and
    # 500 this route.
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - checked_at
    return age >= timedelta(hours=settings.SUBSCRIPTION_RECHECK_HOURS)


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        data = decode_token(payload.refresh_token)
        if data.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user_id = data.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # This is the enforcement clock for cancellations we never hear about
    # directly. A user who unsubscribes by SMS or USSD is still is_subscribed
    # here until something asks BDApps again, and this is the one route every
    # active client hits on a predictable cadence.
    #
    # Deliberately not gated on the result: an unsubscribed user still gets a
    # valid token pair. Refusing here would log them out completely and strand
    # them outside the resubscribe screen — the 403 from get_subscribed_user is
    # what actually withholds the paid features.
    if _recheck_is_due(user.subscription_checked_at):
        status_now = await fetch_subscription_status(user.phone_number)
        # None means we could not reach PHP or BDApps. Leave the stored verdict
        # alone and try again on the next refresh, rather than reading an outage
        # as a mass cancellation.
        if status_now is not None:
            user.is_subscribed = status_now
            user.subscription_checked_at = datetime.now(timezone.utc)
            await db.commit()

    return TokenPair(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
    )

@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user