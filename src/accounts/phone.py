"""Brazilian mobile numbers: one canonical form (11 digits, DDD + 9 + 8 digits) for storage,
lookup and uniqueness, so "(11) 98765-4321", "+55 11 98765 4321" and "11987654321" are the
same number."""

import re
from urllib.parse import quote

from django.core.exceptions import ValidationError

PHONE_LENGTH = 11
COUNTRY_CODE = "55"
# Area codes in use in Brazil. A number outside these is a typo, not a phone.
_AREA_CODES = """
11 12 13 14 15 16 17 18 19 21 22 24 27 28 31 32 33 34 35 37 38
41 42 43 44 45 46 47 48 49 51 53 54 55 61 62 63 64 65 66 67 68 69
71 73 74 75 77 79 81 82 83 84 85 86 87 88 89 91 92 93 94 95 96 97 98 99
"""
AREA_CODES = frozenset(int(code) for code in _AREA_CODES.split())
PHONE_ERROR = "Informe um celular com DDD, por exemplo (11) 98765-4321."

_NON_DIGITS = re.compile(r"\D")


def digits_of(raw: str) -> str:
    """Only the digits of what the person typed, without the country code."""
    digits = _NON_DIGITS.sub("", raw)
    if len(digits) == PHONE_LENGTH + len(COUNTRY_CODE) and digits.startswith(COUNTRY_CODE):
        return digits[len(COUNTRY_CODE) :]
    return digits


def is_valid_phone(digits: str) -> bool:
    return (
        len(digits) == PHONE_LENGTH
        and digits.isascii()
        and digits.isdigit()
        and int(digits[:2]) in AREA_CODES
        and digits[2] == "9"
    )


def normalize_phone(raw: str) -> str:
    """The canonical digits of a valid mobile number; raises ValidationError otherwise."""
    digits = digits_of(raw)
    if not is_valid_phone(digits):
        raise ValidationError(PHONE_ERROR, code="invalid_phone")
    return digits


def validate_phone(value: str) -> None:
    """Model validator: a stored phone is already canonical."""
    if not is_valid_phone(value):
        raise ValidationError(PHONE_ERROR, code="invalid_phone")


def format_phone(digits: str | None) -> str:
    """(11) 98765-4321 for display; anything that is not canonical is returned as it is."""
    if not digits or not is_valid_phone(digits):
        return digits or ""
    return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"


def whatsapp_url(digits: str | None, text: str = "") -> str:
    """Link that opens a WhatsApp chat with the number (message typed in, if given), or "" when
    there is no number."""
    if not digits or not is_valid_phone(digits):
        return ""
    link = f"https://wa.me/{COUNTRY_CODE}{digits}"
    return f"{link}?text={quote(text)}" if text else link
