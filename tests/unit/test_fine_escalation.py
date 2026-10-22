from decimal import Decimal

import pytest

from core.models import DEFAULT_FINE_FACTOR, ClubSettings


def club(base: str) -> ClubSettings:
    return ClubSettings(fine_base=Decimal(base))


def test_factor_is_one_and_a_half() -> None:
    assert Decimal("1.5") == DEFAULT_FINE_FACTOR


def test_each_occurrence_costs_one_and_a_half_times_the_previous() -> None:
    assert club("50.00").fine_examples(5) == [
        Decimal("50.00"),
        Decimal("75.00"),
        Decimal("112.50"),
        Decimal("168.75"),
        Decimal("253.13"),
    ]


def test_values_are_rounded_to_cents_half_up() -> None:
    # 10,01 x 1,5 = 15,015 and x 1,5^2 = 22,5225
    assert club("10.01").fine_amount_for(2) == Decimal("15.02")
    assert club("10.01").fine_amount_for(3) == Decimal("22.52")


def test_there_is_no_ceiling() -> None:
    assert club("50.00").fine_amount_for(10) > club("50.00").fine_amount_for(9)


def test_zero_base_charges_nothing() -> None:
    assert club("0.00").fine_amount_for(4) == Decimal("0.00")


def test_ordinal_starts_at_one() -> None:
    with pytest.raises(ValueError, match="starts at 1"):
        club("50.00").fine_amount_for(0)
