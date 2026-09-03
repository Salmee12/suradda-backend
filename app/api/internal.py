"""Backend-to-backend routes, called only by the PHP tier.

Mounted outside ``settings.API_V1_PREFIX`` and hidden from the schema, because
nothing here is part of the app's public API. The PHP server on cPanel owns the
BDApps credentials and the whitelisted IP; once BDApps confirms an OTP, PHP
calls in here to trade a verified phone number for the same [TokenPair] the rest
of the app already runs on. Flutter never calls these routes and never sees the
secret.

The trust boundary is the shared secret plus, optionally, the caller's IP. It is
worth being blunt about the limit: FastAPI cannot re-verify a consumed OTP, so a
caller who holds the secret can mint a session for any phone number. Hence the
constant-time compare, the allowlist, and the per-number rate limit.
"""

import hmac
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.phone import normalize_msisdn
from app.core.security import create_access_token, create_refresh_token
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import (
    IssueTokenRequest,
    IssueTokenResponse,
    UserExistsResponse,
)

router = APIRouter()

# In-process only, so the window is per worker rather than per deployment. Good
# enough to stop a leaked secret being used to mint tokens in bulk; swap for
# Redis if this ever runs on more than one worker and the limit has to be exact.
_RATE_LIMIT_MAX_CALLS = 5
_RATE_LIMIT_WINDOW_SECONDS = 60
_recent_calls: dict[str, deque[float]] = defaultdict(deque)


def _enforce_rate_limit(key: str) -> None:
    now = time.monotonic()
    hits = _recent_calls[key]
    while hits and now - hits[0] > _RATE_LIMIT_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= _RATE_LIMIT_MAX_CALLS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many token requests for this number",
        )
    hits.append(now)


def _caller_ips(request: Request) -> set[str]:
    """Every address that could plausibly be the caller.

    Render sits behind a proxy, so the peer address is the proxy's and the real
    one is in X-Forwarded-For. XFF is client-appendable, which is precisely why
    the allowlist is defence in depth and not the actual gate.
    """
    ips = {request.client.host} if request.client else set()
    forwarded = request.headers.get("x-forwarded-for", "")
    ips.update(part.strip() for part in forwarded.split(",") if part.strip())
    return ips


async def require_internal_caller(
    request: Request,
    x_internal_secret: str | None = Header(default=None),
) -> None:
    """Gate for every route in this module."""
    if not settings.INTERNAL_SHARED_SECRET:
        # Misconfigured rather than unauthorised: fail closed and say so, so a
        # blank secret can never be matched by sending a blank header.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal endpoints are not configured",
        )

    if not x_internal_secret or not hmac.compare_digest(
        x_internal_secret, settings.INTERNAL_SHARED_SECRET
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal credentials"
        )

    allowed = {ip.strip() for ip in settings.INTERNAL_ALLOWED_IPS.split(",") if ip.strip()}
    if allowed and not (allowed & _caller_ips(request)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Caller address not allowed"
        )


@router.get(
    "/user-exists",
    response_model=UserExistsResponse,
    dependencies=[Depends(require_internal_caller)],
)
async def user_exists(phone_number: str, db: AsyncSession = Depends(get_db)):
    """Whether this number already has an account.

    send_otp.php calls this so the app knows whether to show the "enter your
    name" field on the OTP screen. It doubles as a warm-up: Render's free tier
    sleeps, and waking it here means it is already up by the time the user has
    finished typing the OTP and the verify call needs it.
    """
    try:
        msisdn = normalize_msisdn(phone_number)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await db.execute(select(User.id).where(User.phone_number == msisdn))
    return UserExistsResponse(exists=result.scalars().first() is not None)


@router.post(
    "/issue-token",
    response_model=IssueTokenResponse,
    dependencies=[Depends(require_internal_caller)],
)
async def issue_token(payload: IssueTokenRequest, db: AsyncSession = Depends(get_db)):
    """Trade a BDApps-verified phone number for a session.

    The phone number is already normalised to E.164 by the request schema, which
    is what keeps this from creating a second row for a user who registered
    through the legacy password flow.
    """
    _enforce_rate_limit(payload.phone_number)

    result = await db.execute(select(User).where(User.phone_number == payload.phone_number))
    user = result.scalars().first()
    is_new_user = user is None

    if is_new_user and payload.require_existing:
        # The subscription-status shortcut got here, and there is no account to
        # log in to. Not an error: PHP reads the 404 and falls through to the
        # OTP flow, which is where the display name is collected.
        raise HTTPException(status_code=404, detail="No account for this number yet")

    if is_new_user:
        name = (payload.username or "").strip()
        if not name:
            # The app is expected to have collected this on the OTP screen. A
            # 400 here means the client skipped that step, not that the user
            # did something wrong.
            raise HTTPException(
                status_code=400, detail="username is required for a new subscriber"
            )
        user = User(
            username=name[:50],
            email=None,
            phone_number=payload.phone_number,
            hashed_password=None,
        )
        db.add(user)

    # Only trust BDApps' verdict when PHP actually passed one along; otherwise
    # leave whatever the last check recorded.
    if payload.subscription_status:
        user.is_subscribed = payload.subscription_status.strip().upper() == "REGISTERED"
        user.subscription_checked_at = datetime.now(timezone.utc)

    try:
        await db.commit()
    except IntegrityError:
        # Two verifies for the same new number landed together — a double-tapped
        # button is enough. The other one won; adopt its row rather than 500ing
        # on the unique phone_number index.
        await db.rollback()
        result = await db.execute(
            select(User).where(User.phone_number == payload.phone_number)
        )
        user = result.scalars().first()
        if user is None:
            raise
        is_new_user = False

    await db.refresh(user)

    return IssueTokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
        is_new_user=is_new_user,
        is_subscribed=user.is_subscribed,
    )
