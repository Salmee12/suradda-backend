import uuid
from pydantic import BaseModel, EmailStr, ConfigDict, Field, field_validator

from app.core.phone import normalize_msisdn

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    phone_number: str
    password: str

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return normalize_msisdn(v)

class UserLogin(BaseModel):
    username: str
    password: str

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    username: str
    # Optional: OTP users have no email. Left required here it would raise a
    # ValidationError while serialising GET /auth/me, i.e. a 500 on every
    # OTP-created account.
    email: EmailStr | None = None
    phone_number: str
    is_subscribed: bool

class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class RefreshRequest(BaseModel):
    refresh_token: str


# --- backend-to-backend only (see app/api/internal.py) -----------------------

class IssueTokenRequest(BaseModel):
    """What PHP posts after BDApps confirms an OTP."""

    phone_number: str
    # Required for a phone we've never seen: the app collects the display name
    # on the OTP screen so the row is complete on first insert and there is
    # never an authenticated-but-nameless user. Ignored for existing users —
    # a returning login must not be able to rewrite someone's name.
    username: str | None = Field(default=None, max_length=50)
    # BDApps' own verdict, passed straight through from the verify response, so
    # the very first token already knows the subscription state.
    subscription_status: str | None = None
    # Set by the "already REGISTERED, skip the OTP" path in
    # check_subscription.php. That path has no username to offer, because the
    # name is only ever collected on the OTP screen, so creating a row from it
    # is impossible. With this flag an unknown number gets a clean 404 that PHP
    # can read as "fall through to the OTP flow" instead of a 400 that looks
    # like a bug.
    require_existing: bool = False

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return normalize_msisdn(v)


class IssueTokenResponse(TokenPair):
    """[TokenPair] plus context PHP may want to relay. Flutter reads only the
    two token fields, so the extras are additive and break nothing."""

    is_new_user: bool
    is_subscribed: bool


class UserExistsResponse(BaseModel):
    exists: bool