"""Money formatting and the dates charges are due on."""

from datetime import date
from decimal import Decimal

import pytest

from core.templatetags.money import brl
from finance.charges import (
    month_start,
    monthly_due_date,
    next_month,
    next_saturday_after,
    previous_month,
    saturday_before,
)


def test_sunday_charge_is_due_the_saturday_before() -> None:
    assert saturday_before(date(2026, 10, 18)) == date(2026, 10, 17)


@pytest.mark.parametrize(
    ("day", "saturday"),
    [
        (date(2026, 10, 18), date(2026, 10, 24)),  # Sunday -> next Saturday
        (date(2026, 10, 23), date(2026, 10, 24)),  # Friday -> next day
        (date(2026, 10, 24), date(2026, 10, 31)),  # Saturday -> the following one
    ],
)
def test_fine_is_due_the_next_saturday(day: date, saturday: date) -> None:
    assert next_saturday_after(day) == saturday


def test_month_navigation() -> None:
    assert month_start(date(2026, 10, 31)) == date(2026, 10, 1)
    assert next_month(date(2026, 12, 1)) == date(2027, 1, 1)
    assert next_month(date(2026, 1, 1)) == date(2026, 2, 1)
    assert previous_month(date(2026, 1, 1)) == date(2025, 12, 1)
    assert previous_month(date(2026, 3, 1)) == date(2026, 2, 1)


def test_monthly_due_date() -> None:
    assert monthly_due_date(date(2026, 2, 1), 28) == date(2026, 2, 28)
    assert monthly_due_date(date(2026, 10, 1), 10) == date(2026, 10, 10)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal("30"), "R$ 30,00"),
        (Decimal("1234.5"), "R$ 1.234,50"),
        (Decimal("-620.00"), "-R$ 620,00"),
        (None, "R$ 0,00"),
    ],
)
def test_brl(value: Decimal | None, text: str) -> None:
    assert brl(value) == text
