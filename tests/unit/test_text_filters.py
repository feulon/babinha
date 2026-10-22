import pytest

from core.templatetags.text import stop


@pytest.mark.parametrize(
    ("typed", "shown"),
    [
        ("Chuva", "Chuva."),
        ("Chuva forte.", "Chuva forte."),
        ("Valor errado!", "Valor errado!"),
        ("Será que foi?", "Será que foi?"),
        ("  espaços  ", "espaços."),
        ("", ""),
        (None, ""),
    ],
)
def test_a_typed_reason_ends_with_exactly_one_stop(typed: str | None, shown: str) -> None:
    assert stop(typed) == shown
