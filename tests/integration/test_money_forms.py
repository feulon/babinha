"""Valores e Pix and Despesas: how the forms read and explain amounts and dates."""

from datetime import date
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from core.models import ClubSettings
from finance.models import Expense

pytestmark = pytest.mark.django_db

SETTINGS = reverse("board:settings")
EXPENSES = reverse("board:expenses")


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


def values(**overrides: str) -> dict[str, str]:
    data = {
        "monthly_fee": "100,00",
        "single_fee": "30,00",
        "fine_base": "10,00",
        "fine_factor": "1,50",
        "late_fine_15": "10,00",
        "late_fine_30": "25,00",
        "monthly_due_day": "10",
        "avulso_after_games": "5",
        "pix_key": "",
        "pix_holder": "",
        "board_contact_phone": "",
        "venue_name": "",
    }
    return {**data, **overrides}


class TestValues:
    def test_thousand_separator_is_accepted(self, board_client: Client, clock: FixedClock) -> None:
        response = board_client.post(SETTINGS, values(monthly_fee="1.000,5"))
        assert response.status_code == 302
        assert ClubSettings.load().monthly_fee == Decimal("1000.50")

    def test_currency_sign_and_dot_decimal_are_accepted(
        self, board_client: Client, clock: FixedClock
    ) -> None:
        board_client.post(SETTINGS, values(single_fee="R$ 35.50"))
        assert ClubSettings.load().single_fee == Decimal("35.50")

    def test_nonsense_gets_a_plain_message(self, board_client: Client, clock: FixedClock) -> None:
        html = board_client.post(SETTINGS, values(monthly_fee="muito")).content.decode()
        assert "Informe um valor em reais, por exemplo 25,00." in html

    def test_negative_gets_a_plain_message(self, board_client: Client, clock: FixedClock) -> None:
        html = board_client.post(SETTINGS, values(fine_base="-5")).content.decode()
        assert "O valor não pode ser negativo." in html
        assert "maior ou igual a 0.00" not in html

    def test_the_fine_multiplier_is_editable_and_drives_the_escalation(
        self, board_client: Client, clock: FixedClock
    ) -> None:
        response = board_client.post(SETTINGS, values(fine_base="20,00", fine_factor="2"))
        assert response.status_code == 302
        club = ClubSettings.load()
        assert club.fine_factor == Decimal("2.00")
        assert club.fine_examples(3) == [Decimal("20.00"), Decimal("40.00"), Decimal("80.00")]

    def test_a_multiplier_below_one_is_refused(
        self, board_client: Client, clock: FixedClock
    ) -> None:
        html = board_client.post(SETTINGS, values(fine_factor="0,5")).content.decode()
        assert "O multiplicador não pode ser menor que 1,00." in html

    def test_too_big_gets_a_plain_message(self, board_client: Client, clock: FixedClock) -> None:
        html = board_client.post(SETTINGS, values(monthly_fee="12.345.678,00")).content.decode()
        assert "Valor alto demais. O máximo é 999.999,99." in html

    def test_the_board_contact_is_saved_as_digits_and_shown_formatted(
        self, board_client: Client, clock: FixedClock
    ) -> None:
        board_client.post(SETTINGS, values(board_contact_phone="(11) 98765-4321"))
        assert ClubSettings.load().board_contact_phone == "11987654321"
        assert "(11) 98765-4321" in board_client.get(SETTINGS).content.decode()

    def test_the_board_contact_is_optional_but_must_be_a_phone(
        self, board_client: Client, clock: FixedClock
    ) -> None:
        assert board_client.post(SETTINGS, values()).status_code == 302
        html = board_client.post(SETTINGS, values(board_contact_phone="123")).content.decode()
        assert "Informe um celular com DDD" in html


class TestExpenseDate:
    def post(self, client: Client, day: str, **extra: str):
        data = {"amount": "50,00", "description": "Bola", "date": day, **extra}
        return client.post(EXPENSES, data)

    def test_a_normal_date_is_saved(self, board_client: Client, clock: FixedClock) -> None:
        assert self.post(board_client, "2026-10-10").status_code == 302
        assert Expense.objects.get().date == date(2026, 10, 10)

    def test_a_far_future_date_asks_for_confirmation(
        self, board_client: Client, clock: FixedClock
    ) -> None:
        response = self.post(board_client, "2027-10-10")
        html = response.content.decode()
        assert response.status_code == 200
        assert "A data 10/10/2027 é no futuro." in html
        assert "A data está certa, lançar mesmo assim" in html
        assert not Expense.objects.exists()

    def test_confirming_saves_it_and_says_which_month_it_went_to(
        self, board_client: Client, clock: FixedClock
    ) -> None:
        response = self.post(board_client, "2027-10-10", confirm_date="on")
        assert response.status_code == 302
        page = board_client.get(response["Location"]).content.decode()
        assert "Despesa lançada em Outubro/2027." in page
        assert Expense.objects.get().date == date(2027, 10, 10)

    def test_a_date_over_a_year_ago_asks_for_confirmation(
        self, board_client: Client, clock: FixedClock
    ) -> None:
        html = self.post(board_client, "2025-10-10").content.decode()
        assert "A data 10/10/2025 é de mais de um ano atrás." in html
        assert not Expense.objects.exists()

    def test_a_current_month_expense_keeps_the_short_message(
        self, board_client: Client, clock: FixedClock
    ) -> None:
        response = self.post(board_client, "2026-10-10")
        page = board_client.get(response["Location"]).content.decode()
        assert "Despesa lançada." in page

    def test_editing_an_old_expense_without_touching_its_date_does_not_ask(
        self, board_client: Client, board_member: Player, clock: FixedClock
    ) -> None:
        expense = Expense.objects.create(
            amount=Decimal("10.00"),
            description="Antiga",
            date=date(2024, 1, 5),
            created_by=board_member,
            updated_by=board_member,
        )
        url = reverse("board:expense_edit", args=[expense.pk])
        data = {"amount": "12,00", "description": "Antiga", "date": "2024-01-05"}
        assert board_client.post(url, data).status_code == 302
        expense.refresh_from_db()
        assert expense.amount == Decimal("12.00")
