import random
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from board import tiles
from core.models import ClubSettings
from finance.models import Charge, ChargeKind
from matchday import services
from tests.conftest import Sunday

pytestmark = pytest.mark.django_db

DAY = "2026-10-18"


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday]) -> Sunday:
    return fill_sunday()


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


def groups(client: Client) -> dict[str, dict[str, tiles.Tile]]:
    response = client.get(reverse("board:dashboard"))
    return {g.title: {t.title: t for t in g.tiles} for g in response.context["groups"]}


class TestDashboardTiles:
    def test_players_still_cannot_open_it(self, make_player) -> None:
        client = Client()
        client.force_login(make_player("Jogador comum"))
        assert client.get(reverse("board:dashboard")).status_code == 403

    def test_sunday_without_teams_asks_for_attention(
        self, sunday: Sunday, board_client: Client, board_member: Player
    ) -> None:
        assert groups(board_client)["Domingo 18/10"]["Times"].note == "Sem times"
        services.draw(sunday.game_day.pk, board_member, random.Random(5))
        assert groups(board_client)["Domingo 18/10"]["Times"].note == "Falta confirmar"
        services.confirm(sunday.game_day.pk, board_member)
        tile = groups(board_client)["Domingo 18/10"]["Times"]
        assert (tile.note, tile.needs_attention) == ("Confirmados", False)

    def test_players_without_a_phone_are_flagged(
        self, make_player, board_client: Client, clock
    ) -> None:
        assert not groups(board_client)["Jogadores"]["Jogadores"].needs_attention
        legacy = make_player("Sem celular")
        Player.objects.filter(pk=legacy.pk).update(phone=None)
        tile = groups(board_client)["Jogadores"]["Jogadores"]
        assert (tile.note, tile.tone) == ("1 sem celular", tiles.WAIT)

    def test_money_tiles_flag_what_needs_attention(self, board_client: Client, clock) -> None:
        money = groups(board_client)["Dinheiro"]
        assert money["Configurações"].note == "Falta configurar"
        assert not money["Comprovantes"].needs_attention
        ClubSettings.objects.update_or_create(
            pk=ClubSettings.load().pk, defaults={"pix_key": "chave@baba.com.br"}
        )
        html = board_client.get(reverse("board:dashboard")).content.decode()
        assert 'data-attention="wait"' in html

    def test_the_money_tile_shows_count_and_total_in_reais(
        self, board_client: Client, make_player, club_values: ClubSettings, clock
    ) -> None:
        player = make_player("Garrincha")
        for month in (8, 9):
            Charge.objects.create(
                player=player,
                kind=ChargeKind.MONTHLY,
                amount=Decimal("600.25"),
                reference_date=date(2026, month, 1),
                due_date=date(2026, month, 10),
            )
        tile = groups(board_client)["Dinheiro"]["A receber"]
        assert tile.note == "2 em aberto · R$\u00a01.200,50"


class TestTutorial:
    def test_players_cannot_open_it(self, client: Client, make_player) -> None:
        client.force_login(make_player("Zeca"))
        assert client.get(reverse("board:tutorial")).status_code == 403
