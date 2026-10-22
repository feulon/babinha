"""Diretoria "Cobrar no grupo": one WhatsApp message tagging everybody who owes."""

from urllib.parse import quote

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from core.models import ClubSettings
from finance import group_charge
from finance.reminders import debtors
from finance.selectors import PixInfo, open_charges
from games import services
from games.models import GameDay
from tests.conftest import PIX_KEY, end_game

pytestmark = pytest.mark.django_db

URL = reverse("board:group_charge")


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def owing(clock: FixedClock, club_values: ClubSettings, make_player, president: Player):
    """Garrincha (formatted phone stored canonical) and Didi (no phone) played and owe R$ 30."""
    garrincha = make_player("Garrincha", referred_by=president, phone="11987654321")
    didi = make_player("Didi", referred_by=president)
    Player.objects.filter(pk=didi.pk).update(phone=None)
    didi.refresh_from_db()
    day = services.current_game_day().pk
    services.join_list(day, garrincha)
    services.join_list(day, didi)
    end_game(GameDay.objects.get(pk=day))
    return garrincha, didi


def message_of(client: Client) -> str:
    html = client.get(URL).content.decode()
    return html.split("data-group-message>", 1)[1].split("</pre>", 1)[0]


class TestAccess:
    def test_a_player_cannot_open_it(self, client: Client, make_player, clock) -> None:
        client.force_login(make_player("Jogador"))
        assert client.get(URL).status_code == 403

    def test_anonymous_is_sent_to_login(self, client: Client) -> None:
        assert client.get(URL).status_code == 302


class TestMessage:
    def test_tags_each_debtor_with_country_code_and_digits_only(
        self, board_client: Client, owing
    ) -> None:
        message = message_of(board_client)
        assert "@5511987654321 Garrincha" in message
        assert "(11)" not in message

    def test_a_debtor_without_phone_is_listed_untagged_and_flagged(
        self, board_client: Client, owing
    ) -> None:
        html = board_client.get(URL).content.decode()
        assert "\nDidi - " in message_of(board_client)
        assert "Sem telefone: 1" in html

    def test_amounts_total_and_pix(self, board_client: Client, owing) -> None:
        message = message_of(board_client)
        assert "Garrincha - " in message
        assert message.count("R$ 30,00") == 2
        assert "Total devido: R$ 60,00" in message
        assert f"Pix: {PIX_KEY} (em nome de Presidente)" in message

    def test_only_players_with_something_to_pay_enter(
        self, board_client: Client, owing, make_player
    ) -> None:
        make_player("Quitado")
        assert "Quitado" not in message_of(board_client)

    def test_nobody_owing_says_everything_is_paid(self, board_client: Client, clock) -> None:
        html = board_client.get(URL).content.decode()
        assert "Tudo pago" in html
        assert "Compartilhar no WhatsApp" not in html

    def test_viewing_it_changes_nothing(self, board_client: Client, owing, clock) -> None:
        today = clock.now().date()

        def snapshot():
            return [(r.charge.pk, r.charge.amount, r.charge.status) for r in open_charges(today)]

        before = snapshot()
        board_client.get(URL)
        assert before == snapshot()


class TestCompose:
    def test_no_pix_line_without_a_key(self, owing, clock) -> None:
        message = group_charge.compose(debtors(open_charges(clock.now().date())), PixInfo("", ""))
        assert "Pix" not in message

    def test_share_url_encodes_the_message(self) -> None:
        assert (
            group_charge.whatsapp_share_url("a b\n@55")
            == f"https://wa.me/?text={quote('a b\n@55')}"
        )

    def test_mention_ignores_an_invalid_phone(self) -> None:
        assert group_charge.mention("123") == ""
        assert group_charge.mention(None) == ""
        assert group_charge.mention("11987654321") == "@5511987654321"
