from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.core.security import decode_token
from app.models.user import User

bearer_scheme = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = credentials.credentials
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise credentials_exception
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception

    # Deliberately a 401 and not a 403: a disabled account should look exactly
    # like a bad token, and the client already knows how to handle 401 by
    # clearing its tokens. 403 is reserved for the subscription gate below,
    # which the app has to react to differently.
    if not user.is_active:
        raise credentials_exception

    return user


async def get_subscribed_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Like [get_current_user], but only for users who are actually paying.

    Guards the surface the 2.78tk/day is meant to buy. Everything else — reading
    your own profile, leaving a room you are already in — stays on
    [get_current_user] so a lapsed subscriber can still see their state and back
    out cleanly instead of hitting a wall mid-session.

    403 is used by nothing else in this API, which is what lets
    ``AuthInterceptor`` treat it as "subscription lapsed" without parsing the
    body. Keep it that way.
    """
    if not current_user.is_subscribed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An active subscription is required for this feature.",
        )
    return current_user
