"""The Pix holder is a name the presidente writes in Valores e Pix, never the presidente's own."""

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from core.models import ClubSettings
from finance.group_charge import compose
from finance.reminders import debtors
from finance.selectors import PixInfo, open_charges, pix_info
from games import services
from games.models import GameDay
from tests.conftest import PIX_HOLDER, PIX_KEY, end_game
from tests.integration.test_money_forms import values

pytestmark = pytest.mark.django_db

SETTINGS = reverse("board:settings")
PAYMENTS = reverse("finance:payments")
REMINDERS = reverse("board:reminders")


def login(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


@pytest.fixture
def owing(clock: FixedClock, club_values: ClubSettings, make_player, president: Player):
    player = make_player("Garrincha", referred_by=president, email="garrincha@example.com")
    day = services.current_game_day().pk
    services.join_list(day, player)
    end_game(GameDay.objects.get(pk=day))
    return player


def clear_holder() -> None:
    ClubSettings.objects.filter(pk=ClubSettings.SINGLETON_PK).update(pix_holder="")


class TestSettings:
    def test_the_presidente_saves_the_holder_next_to_the_key(
        self, president: Player, clock: FixedClock
    ) -> None:
        client = login(president)
        html = client.get(SETTINGS).content.decode()
        assert "Nome do titular do Pix" in html
        assert html.index('name="pix_key"') < html.index('name="pix_holder"')
        data = values(pix_key=PIX_KEY, pix_holder="  Maria   da Silva ")
        assert client.post(SETTINGS, data).status_code == 302
        assert ClubSettings.load().pix_holder == "Maria da Silva"

    def test_the_holder_is_optional(self, president: Player, clock: FixedClock) -> None:
        ClubSettings.objects.filter(pk=1).update(pix_holder="Alguém")
        assert login(president).post(SETTINGS, values(pix_holder="")).status_code == 302
        assert ClubSettings.load().pix_holder == ""

    def test_the_holder_has_a_length_limit(self, president: Player, clock: FixedClock) -> None:
        response = login(president).post(SETTINGS, values(pix_holder="x" * 81))
        assert response.status_code == 200
        assert ClubSettings.load().pix_holder == ""

    def test_the_rest_of_the_diretoria_cannot_change_it(
        self, board_member: Player, club_values: ClubSettings
    ) -> None:
        client = login(board_member)
        html = client.get(SETTINGS).content.decode()
        assert "Só o presidente altera o titular do Pix." in html
        data = values(pix_key="outra@chave.com", pix_holder="Intruso")
        assert client.post(SETTINGS, data).status_code == 302
        club = ClubSettings.load()
        assert club.pix_holder == PIX_HOLDER
        assert club.pix_key == PIX_KEY


class TestWhereItShows:
    def test_pix_info_uses_the_holder_not_the_presidente(
        self, president: Player, club_values: ClubSettings
    ) -> None:
        assert pix_info() == PixInfo(PIX_KEY, PIX_HOLDER)
        assert president.name not in pix_info().holder

    def test_empty_holder_does_not_fall_back_to_the_presidente(
        self, president: Player, club_values: ClubSettings
    ) -> None:
        clear_holder()
        assert pix_info() == PixInfo(PIX_KEY, "")

    def test_the_pix_card_names_the_holder(self, owing: Player) -> None:
        html = login(owing).get(PAYMENTS).content.decode()
        assert PIX_KEY in html
        assert f"Em nome de {PIX_HOLDER}" in html

    def test_the_pix_card_hides_the_line_without_a_holder(self, owing: Player) -> None:
        clear_holder()
        html = login(owing).get(PAYMENTS).content.decode()
        assert PIX_KEY in html
        assert "Em nome de" not in html
        assert "pix__holder" not in html

    def test_the_group_charge_names_the_holder(self, owing: Player) -> None:
        message = compose(debtors(open_charges(GameDay.objects.get().date)), pix_info())
        assert f"Pix: {PIX_KEY} (em nome de {PIX_HOLDER})" in message

    def test_the_group_charge_has_no_holder_when_empty(self, owing: Player) -> None:
        clear_holder()
        message = compose(debtors(open_charges(GameDay.objects.get().date)), pix_info())
        assert message.endswith(f"Pix: {PIX_KEY}")
        assert "em nome de" not in message

    def test_the_reminder_email_names_the_holder(self, board_member: Player, owing: Player) -> None:
        data = {"step": "send", "player": [owing.pk]}
        login(board_member).post(REMINDERS, data)
        assert f"Pix: {PIX_KEY} ({PIX_HOLDER})" in mail.outbox[0].body

    def test_the_reminder_email_has_no_holder_when_empty(
        self, board_member: Player, owing: Player
    ) -> None:
        clear_holder()
        data = {"step": "send", "player": [owing.pk]}
        login(board_member).post(REMINDERS, data)
        body = mail.outbox[0].body
        assert f"Pix: {PIX_KEY}\n" in body
        assert "(" not in body.split("Pix:", 1)[1].split("\n", 1)[0]
