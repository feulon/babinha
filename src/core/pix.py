"""Pix key validation. A typo in the key sends everyone's money nowhere, so it is checked.

Keys are stored in their canonical form, which every bank app accepts:
CPF as 11 digits, CNPJ as 14 characters, phone as +55 plus DDD and number, e-mail in lowercase
and the random key (EVP) as a lowercase UUID.
"""

import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

MAX_LENGTH = 77
_PUNCTUATION = re.compile(r"[\s.\-/()]")
_EVP = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_PHONE = re.compile(r"^\+55\d{10,11}$")
_CNPJ = re.compile(r"^[0-9A-Z]{12}\d{2}$")  # alphanumeric CNPJ (2026+) keeps numeric digits


def _check_digit(values: list[int], weights: range | list[int]) -> int:
    remainder = sum(v * w for v, w in zip(values, weights, strict=True)) % 11
    return 0 if remainder < 2 else 11 - remainder


def is_valid_cpf(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit() or len(set(digits)) == 1:
        return False
    values = [int(d) for d in digits]
    first = _check_digit(values[:9], range(10, 1, -1))
    second = _check_digit(values[:10], range(11, 1, -1))
    return values[9] == first and values[10] == second


def is_valid_cnpj(value: str) -> bool:
    if not _CNPJ.match(value) or len(set(value)) == 1:
        return False
    values = [ord(c) - ord("0") for c in value]
    weights = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    first = _check_digit(values[:12], weights[1:])
    second = _check_digit(values[:13], weights)
    return values[12] == first and values[13] == second


def normalize_pix_key(raw: str) -> str:
    """Canonical form of a Pix key, or ValidationError explaining what is wrong."""
    key = raw.strip()
    if not key:
        raise ValidationError("Informe a chave Pix.")
    if "@" in key:
        email = key.lower()
        try:
            validate_email(email)
        except ValidationError as exc:
            raise ValidationError("E-mail inválido para chave Pix.") from exc
        if len(email) > MAX_LENGTH:
            raise ValidationError("E-mail longo demais para chave Pix.")
        return email
    if _EVP.match(key.lower()):
        return key.lower()
    compact = _PUNCTUATION.sub("", key).upper()
    if compact.startswith("+"):
        if not _PHONE.match(compact):
            raise ValidationError("Celular inválido. Use +55, o DDD e o número.")
        return compact
    if len(compact) == 11 and compact.isdigit():
        if not is_valid_cpf(compact):
            raise ValidationError("CPF inválido. Para celular, use +55 e o DDD.")
        return compact
    if len(compact) == 14:
        if not is_valid_cnpj(compact):
            raise ValidationError("CNPJ inválido.")
        return compact
    raise ValidationError(
        "Chave Pix inválida. Use e-mail, celular (+55...), CPF, CNPJ ou chave aleatória."
    )


def validate_pix_key(value: str) -> None:
    """Model validator: the stored key must already be canonical."""
    if value and normalize_pix_key(value) != value:
        raise ValidationError("Chave Pix fora do formato padrão.")
