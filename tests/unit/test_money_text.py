import pytest

from core.forms import normalize_money_text


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("25,00", "25,00"),
        ("25", "25"),
        ("1.000,5", "1000,5"),
        ("1.234.567,89", "1234567,89"),
        ("1.000", "1000"),
        ("12.345.678", "12345678"),
        ("R$ 1.000,50", "1000,50"),
        ("  30 ", "30"),
        # A single dot that is not a group of three digits is a decimal point.
        ("25.50", "25.50"),
        ("1.5", "1.5"),
        ("abc", "abc"),
        ("", ""),
    ],
)
def test_normalize_money_text(typed: str, expected: str) -> None:
    assert normalize_money_text(typed) == expected
