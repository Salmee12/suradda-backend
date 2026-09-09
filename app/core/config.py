from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "SurAdda"
    API_V1_PREFIX: str = "/api/v1"

    DATABASE_URL: str

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_HOURS: int = 48

    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

    # Shared with the PHP tier only, so it can mint a session after BDApps has
    # verified an OTP. Never shipped to Flutter.
    #
    # Defaulted to "" rather than made required on purpose: a required setting
    # missing from .env would stop the already-deployed app from booting at all.
    # The internal routes refuse to serve while it is blank, so the failure mode
    # is a closed door rather than an outage.
    INTERNAL_SHARED_SECRET: str = ""
    # Optional comma-separated allowlist of caller IPs (the cPanel server).
    # Defence in depth behind the secret — leave blank to disable.
    INTERNAL_ALLOWED_IPS: str = ""

    # Gate for the catalogue-management routes under /admin. Same reasoning as
    # INTERNAL_SHARED_SECRET: defaulted blank so a missing value cannot stop the
    # deployed app from booting, and the routes refuse to serve while it is
    # blank rather than accepting a blank header.
    #
    # This is the only thing standing between the public internet and DELETE on
    # the song catalogue, so it wants real entropy — 32+ random bytes, not a
    # password. It is never sent to Flutter.
    ADMIN_API_KEY: str = ""
    # Optional comma-separated allowlist of admin caller IPs. Useful if you
    # always administer from one place; leave blank to disable.
    ADMIN_ALLOWED_IPS: str = ""

    # Hard ceiling on an uploaded audio file, in megabytes. Render's free tier
    # has little headroom, and the upload is streamed to Cloudinary rather than
    # buffered, so this guards the request body rather than our own memory.
    MAX_UPLOAD_MB: int = 25

    # Where the PHP tier lives, for the server-side subscription re-check.
    # PHP holds the BDApps credentials; FastAPI never talks to BDApps directly.
    PHP_API_BASE_URL: str = "https://bdappsdigitalapps.com/shuradda_subscription_apis"

    # How stale is_subscribed may get before /auth/refresh re-checks it against
    # BDApps. Access tokens last 30 minutes, so re-checking on every refresh
    # would mean a telco round trip every half hour per active user.
    SUBSCRIPTION_RECHECK_HOURS: int = 12

    class Config:
        env_file = ".env"

settings = Settings()