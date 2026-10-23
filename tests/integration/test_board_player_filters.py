"""Filters of the diretoria's player list: each one, their combination, htmx and permissions."""

import re
from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player, PlayerType, Position

pytestmark = pytest.mark.django_db

PLAYERS = reverse("board:players")


@pytest.fixture
def squad(make_player: Callable[..., Player], president: Player) -> dict[str, Player]:
    return {
        "zico": make_player("Zico Mensal", player_type=PlayerType.MENSALISTA, pote=1),
        "careca": make_player("Careca Avulso", pote=5, referred_by=president),
        "xuxa": make_player("Xuxa Agregada", player_type=PlayerType.AGREGADO, pote=3),
        "gil": make_player(
            "Gilmar Goleiro", position=Position.GOLEIRO, referred_by=president, pote=3
        ),
    }


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


def listed(response) -> set[str]:
    html = response.content.decode()
    return set(re.findall(r"<strong>([^<]+)</strong>", html))


def shown(board_client: Client, squad: dict[str, Player], **filters: str) -> set[str]:
    names = listed(board_client.get(PLAYERS, filters))
    return {key for key, player in squad.items() if player.name in names}


class TestEachFilter:
    def test_no_filter_lists_everyone(self, board_client: Client, squad) -> None:
        assert shown(board_client, squad) == set(squad)

    def test_search_matches_part_of_the_name_ignoring_case(
        self, board_client: Client, squad
    ) -> None:
        assert shown(board_client, squad, q="CARE") == {"careca"}

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [("mensalista", {"zico"}), ("agregado", {"xuxa"}), ("avulso", {"careca", "gil"})],
    )
    def test_type(self, board_client: Client, squad, kind: str, expected: set[str]) -> None:
        assert shown(board_client, squad, tipo=kind) == expected

    def test_position(self, board_client: Client, squad) -> None:
        assert shown(board_client, squad, posicao="goleiro") == {"gil"}
        assert shown(board_client, squad, posicao="linha") == {"zico", "careca", "xuxa"}

    @pytest.mark.parametrize(("pote", "expected"), [("1", {"zico"}), ("5", {"careca"})])
    def test_pote(self, board_client: Client, squad, pote: str, expected: set[str]) -> None:
        assert shown(board_client, squad, pote=pote) == expected

    def test_goalkeeper_never_matches_a_pote(self, board_client: Client, squad) -> None:
        assert squad["gil"].pote == 3
        assert shown(board_client, squad, pote="3") == {"xuxa"}

    def test_without_referrer(self, board_client: Client, squad, president: Player) -> None:
        for player in squad.values():
            player.referred_by = None if player is squad["xuxa"] else president
            player.save()
        assert shown(board_client, squad, sem_indicador="1") == {"xuxa"}

    def test_senseless_values_are_ignored(self, board_client: Client, squad) -> None:
        response = board_client.get(PLAYERS, {"tipo": "x", "pote": "9", "posicao": "?"})
        assert response.status_code == 200
        assert shown(board_client, squad, tipo="x", pote="9", posicao="?") == set(squad)


class TestCombination:
    def test_filters_narrow_each_other(self, board_client: Client, squad) -> None:
        assert shown(board_client, squad, tipo="avulso", posicao="linha") == {"careca"}
        assert shown(board_client, squad, tipo="avulso", pote="3") == set()
        assert shown(board_client, squad, tipo="agregado", pote="3") == {"xuxa"}
        assert shown(board_client, squad, q="a", tipo="avulso", pote="5") == {"careca"}
        assert shown(board_client, squad, tipo="mensalista", pote="5") == set()

    def test_counter_and_empty_state(self, board_client: Client, squad) -> None:
        html = board_client.get(PLAYERS, {"tipo": "avulso", "posicao": "linha"}).content.decode()
        match = re.search(r"(\d+) de (\d+) jogador", html)
        assert match
        assert int(match[1]) == len(
            listed(board_client.get(PLAYERS, {"tipo": "avulso", "posicao": "linha"}))
        )
        assert int(match[2]) == Player.objects.count()
        empty = board_client.get(PLAYERS, {"q": "ninguem"}).content.decode()
        assert "Nenhum jogador encontrado" in empty
        assert re.search(r"0 de \d+ jogador", empty)


class TestHtmx:
    def test_htmx_gets_only_the_swappable_block(self, board_client: Client, squad) -> None:
        response = board_client.get(PLAYERS, {"tipo": "agregado"}, HTTP_HX_REQUEST="true")
        html = response.content.decode()
        assert html.lstrip().startswith('<div id="board-players"')
        assert "<html" not in html
        assert "Xuxa Agregada" in html
        assert "Zico Mensal" not in html
        assert "HX-Request" in response["Vary"]


class TestEditingKeepsTheFilter:
    def test_saving_returns_to_the_filtered_list(self, board_client: Client, squad) -> None:
        careca = squad["careca"]
        url = reverse("board:player_edit", kwargs={"player_id": careca.pk}) + "?tipo=avulso&pote=5"
        response = board_client.post(
            url,
            {
                "name": careca.name,
                "nickname": careca.nickname,
                "is_active": "on",
                "phone": careca.phone,
                "player_type": "avulso",
                "position": "linha",
                "pote": "2",
                "referred_by": careca.referred_by_id,
            },
        )
        assert response.status_code == 302
        assert response["Location"] == f"{PLAYERS}?tipo=avulso&pote=5"
        careca.refresh_from_db()
        assert careca.pote == 2

    def test_a_foreign_return_address_is_never_followed(self, board_client: Client, squad) -> None:
        careca = squad["careca"]
        url = reverse("board:player_edit", kwargs={"player_id": careca.pk})
        response = board_client.post(
            f"{url}?next=https://evil.example&tipo=avulso",
            {
                "name": careca.name,
                "nickname": careca.nickname,
                "is_active": "on",
                "phone": careca.phone,
                "player_type": "avulso",
                "position": "linha",
                "pote": "2",
                "referred_by": careca.referred_by_id,
            },
        )
        assert response["Location"] == f"{PLAYERS}?tipo=avulso"


class TestPermissions:
    def test_players_cannot_filter_or_see_pote(
        self, client: Client, make_player, squad, president: Player
    ) -> None:
        client.force_login(squad["careca"])
        for params in ({}, {"pote": "5"}, {"q": "x"}):
            assert client.get(PLAYERS, params).status_code == 403
            assert client.get(PLAYERS, params, HTTP_HX_REQUEST="true").status_code == 403

    def test_anonymous_goes_to_login(self, client: Client) -> None:
        response = client.get(PLAYERS, {"pote": "5"})
        assert response.status_code == 302
        assert reverse("accounts:login") in response["Location"]

    def test_pote_filter_does_not_leak_on_the_public_list(self, client: Client, squad) -> None:
        client.force_login(squad["careca"])
        html = client.get(reverse("community:players"), {"pote": "5"}).content.decode()
        assert "Pote" not in html
