"""Canonical phone-number handling.

Three tiers hand us phone numbers in three different shapes: the PHP backend
normalises to the local ``01XXXXXXXXX`` form (and builds ``tel:88…`` for
BDApps), the Flutter register page asks for E.164, and rows created before OTP
auth all hold E.164. If any tier looks a user up in a shape another tier didn't
store, the lookup misses and we create a duplicate account for someone who
already exists — so every write and every lookup goes through here first.

E.164 is the stored form because it is unambiguous without knowing the region.
"""

import re

import phonenumbers

# Bare local numbers ("01712345678") carry no country code, so parsing needs a
# region hint. Everything this app talks to is Bangladeshi.
DEFAULT_REGION = "BD"

_NON_DIGITS = re.compile(r"\D")


def normalize_msisdn(raw: str) -> str:
    """Return [raw] as E.164, e.g. ``+8801712345678``.

    Accepts the local form, the country-code form with or without ``+``, and
    BDApps' ``tel:`` prefix. Raises [ValueError] if the result isn't a valid
    number, so callers can turn that into a 400 rather than storing junk.
    """
    if not raw or not raw.strip():
        raise ValueError("Phone number is required")

    digits = _NON_DIGITS.sub("", raw)
    if not digits:
        raise ValueError("Phone number contains no digits")

    # Decide whether we can hand phonenumbers an international string or need to
    # fall back to the region hint. "880…" is a country code even without the
    # plus; a leading "0" means it's national.
    if raw.strip().startswith("+") or digits.startswith("880"):
        candidate, region = "+" + digits, None
    else:
        candidate, region = digits, DEFAULT_REGION

    try:
        parsed = phonenumbers.parse(candidate, region)
    except phonenumbers.NumberParseException as exc:
        raise ValueError("Phone number could not be parsed") from exc

    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("Invalid phone number")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
