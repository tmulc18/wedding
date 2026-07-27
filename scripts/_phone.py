"""Shared phone-number helpers for the wedding scripts."""

import re


def normalize_phone(raw: str) -> str | None:
    """Return E.164 form (+countrycode...) or None if unparseable.

    Accepts US numbers as 10 digits or 11 digits starting with 1, any number
    already prefixed with '+', and bare international numbers of 11-15 digits
    (prepended with '+' as E.164). Strips whitespace, dashes, parens, dots.
    """
    digits = re.sub(r"[\s\-\(\)\.]", "", raw.strip())
    if digits.startswith("+"):
        rest = digits[1:]
        return digits if rest.isdigit() and len(rest) >= 10 else None
    if not digits.isdigit():
        return None
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    # Bare international number (e.g. Croatia 385...): treat as E.164.
    if 11 <= len(digits) <= 15:
        return "+" + digits
    return None
