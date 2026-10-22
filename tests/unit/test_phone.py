import pytest
from django.core.exceptions import ValidationError

from accounts.phone import format_phone, normalize_phone, whatsapp_url


@pytest.mark.parametrize(
    "typed",
    [
        "11987654321",
        "(11) 98765-4321",
        "11 98765 4321",
        "11 9 8765-4321",
        " 11.98765.4321 ",
        "+55 11 98765-4321",
        "5511987654321",
        "+55 (11) 98765-4321",
    ],
)
def test_any_formatting_is_one_number(typed: str) -> None:
    assert normalize_phone(typed) == "11987654321"


def test_the_55_area_code_is_not_mistaken_for_the_country_code() -> None:
    assert normalize_phone("55 99123-4567") == "55991234567"
    assert normalize_phone("+55 55 99123-4567") == "55991234567"


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "abc",
        "98765-4321",  # no area code
        "1198765432",  # one digit short: a landline-sized number
        "119876543210",  # one digit too many
        "11 3456-7890",  # landline
        "11 88765-4321",  # a mobile has a 9 after the area code
        "10 98765-4321",  # no such area code
        "20 98765-4321",
        "00 98765-4321",
        "+1 415 555 2671",
        "11 9876a-4321",
    ],
)
def test_rejects_what_is_not_a_brazilian_mobile(typed: str) -> None:
    with pytest.raises(ValidationError):
        normalize_phone(typed)


def test_display_and_whatsapp_link() -> None:
    assert format_phone("11987654321") == "(11) 98765-4321"
    assert whatsapp_url("11987654321") == "https://wa.me/5511987654321"


@pytest.mark.parametrize("missing", [None, ""])
def test_nothing_to_show_without_a_phone(missing: str | None) -> None:
    assert format_phone(missing) == ""
    assert whatsapp_url(missing) == ""
