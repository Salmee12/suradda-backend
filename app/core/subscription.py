"""Server-side subscription re-check, asked of the PHP tier.

FastAPI never talks to BDApps directly — PHP owns the credentials and sits on the
IP BDApps whitelisted. So "is this number still paying?" is a question we ask
cPanel, which asks the telco.

The contract with ``check_subscription.php``:

    POST  user_mobile=01XXXXXXXXX&status_only=1
    ->    {"success": true, "subscribed": true}

``status_only=1`` matters. The app-facing call to that same script mints a
session when the number comes back REGISTERED; if we did not opt out of that
here, a refresh would make PHP turn around and call ``/internal/issue-token``,
producing a token nobody reads. The flag keeps the read path a pure read.

Every failure here returns ``None``, never ``False``. A cPanel outage, a BDApps
timeout or a malformed response must not look like "unsubscribed", or one bad
afternoon on the PHP host would revoke access for every user at once.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# PHP's own BDApps call is allowed 30s, so a slow telco can outlast this. That is
# deliberate: this runs inside a user-facing /auth/refresh, and a stale
# is_subscribed for another 12 hours is a far better outcome than making the
# user stare at a spinner. The re-check simply retries on the next refresh.
_TIMEOUT_SECONDS = 10.0

_TRUTHY = {"1", "true", "yes", "registered", "active", "subscribed"}


def _interpret(data: object) -> bool | None:
    """Pull a subscription verdict out of whatever shape PHP returned.

    Deliberately tolerant about the key: the PHP tier has grown organically and
    reports the verdict as a ``subscribed`` boolean in some responses and a
    ``subscriptionStatus`` string in others. Anything unrecognised is unknown
    rather than unsubscribed.
    """
    if not isinstance(data, dict):
        return None

    if data.get("success") is False:
        return None

    for key in ("subscribed", "is_subscribed"):
        value = data.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip():
            return value.strip().lower() in _TRUTHY

    for key in ("subscriptionStatus", "subscription_status", "status"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower() in _TRUTHY

    return None


async def fetch_subscription_status(phone_number: str) -> bool | None:
    """Ask PHP whether [phone_number] is still subscribed.

    Returns True/False on a clear answer and None when we could not find out —
    callers must treat None as "keep whatever the database already says".
    """
    local = _to_local_msisdn(phone_number)
    url = f"{settings.PHP_API_BASE_URL.rstrip('/')}/check_subscription.php"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url, data={"user_mobile": local, "status_only": "1"}
            )
    except httpx.HTTPError as exc:
        logger.warning("Subscription re-check failed for %s: %s", local, exc)
        return None

    if response.status_code != 200:
        logger.warning(
            "Subscription re-check for %s returned HTTP %s", local, response.status_code
        )
        return None

    try:
        payload = response.json()
    except ValueError:
        # cPanel serves an HTML error page when a script dies, and PHP notices
        # can prepend warnings to otherwise-valid JSON.
        logger.warning("Subscription re-check for %s returned non-JSON", local)
        return None

    return _interpret(payload)


def _to_local_msisdn(e164: str) -> str:
    """``+8801712345678`` -> ``01712345678``.

    The PHP scripts normalise their input anyway, but they were written around
    the 11-digit local form and it is what their logs and BDApps calls expect.
    """
    digits = "".join(ch for ch in e164 if ch.isdigit())
    if digits.startswith("880"):
        return "0" + digits[3:]
    if digits.startswith("88") and len(digits) == 12:
        return "0" + digits[2:]
    return digits
