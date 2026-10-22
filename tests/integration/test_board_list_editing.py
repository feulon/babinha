"""The diretoria edits the Sunday list by hand: order, sections and the spot limit, at any time and
with no fine."""

from collections.abc import Callable
from datetime import datetime

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from accounts.models import Player, Position
from core.clock import FixedClock
from finance.models import Charge
from fines.models import Fine
from games import services as games
from games.domain.roster import Section
from games.domain.types import EntryStatus
from games.models import ListEntry
from games.selectors import board_roster
from tests.conftest import SP, Sunday, end_game

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
DAY = "2026-10-18"
SUNDAY_AFTER_DEADLINE = datetime(2026, 10, 18, 7, 0, tzinfo=SP)


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday]) -> Sunday:
    return fill_sunday()


def move(client: Client, player: Player, section: str, index: int | None = None, **extra):
    data = {"section": section} | ({"index": index} if index is not None else {})
    url = reverse("board:place_on_list", kwargs={"day": DAY, "player_id": player.pk})
    return client.post(url, data, **extra)


def line_names(sunday: Sunday) -> list[str]:
    return [row.player.name for row in board_roster(sunday.game_day).line]


def entry(player: Player) -> ListEntry:
    return ListEntry.objects.get(player=player)


class TestOrder:
    def test_moving_up_swaps_the_roster_block_and_keeps_the_place(
        self, sunday: Sunday, board_client: Client
    ) -> None:
        third = sunday.line[2]
        response = move(board_client, third, "lista-linha", 2, **HTMX)
        html = response.content.decode()
        assert response.status_code == 200
        assert 'id="roster-board"' in html
        assert f"{third.name} agora é o 2º da linha." in html
        assert line_names(sunday)[:3] == ["Linha 01", "Linha 03", "Linha 02"]
        assert entry(third).board_rank == 2

    def test_the_manual_place_survives_new_sign_ups_and_departures(
        self, sunday: Sunday, board_client: Client
    ) -> None:
        last = sunday.line[-1]
        move(board_client, last, "lista-linha", 1)
        games.leave_list(sunday.game_day.pk, sunday.line[0])
        assert line_names(sunday)[:2] == ["Linha 20", "Linha 02"]

    def test_a_plain_form_post_redirects(self, sunday: Sunday, board_client: Client) -> None:
        response = move(board_client, sunday.line[1], "lista-linha", 1)
        assert response.status_code == 302


class TestSections:
    def test_promoting_into_a_full_list_sends_the_last_to_the_queue_with_no_fine(
        self, sunday: Sunday, board_client: Client, make_player: Callable[..., Player]
    ) -> None:
        waiting = make_player("Esperando")
        games.join_list(sunday.game_day.pk, waiting)
        assert entry(waiting).status == EntryStatus.ESPERA
        html = move(board_client, waiting, "lista-linha", **HTMX).content.decode()
        assert entry(waiting).status == EntryStatus.LISTA
        last = sunday.line[-1]
        assert entry(last).status == EntryStatus.ESPERA
        assert entry(last).bumped
        assert f"{last.name} ficou fora das vagas e foi para o topo da fila de espera" in html
        assert not Fine.objects.exists()

    def test_sending_to_the_queue_gives_the_spot_to_the_next(
        self, sunday: Sunday, board_client: Client, make_player: Callable[..., Player]
    ) -> None:
        waiting = make_player("Esperando")
        games.join_list(sunday.game_day.pk, waiting)
        first = sunday.line[0]
        move(board_client, first, "espera-linha")
        assert entry(first).status == EntryStatus.ESPERA
        assert entry(waiting).status == EntryStatus.LISTA
        assert [row.player for row in board_roster(sunday.game_day).waiting_line] == [first]

    def test_sending_to_an_empty_queue_explains_why_not(
        self, sunday: Sunday, board_client: Client
    ) -> None:
        html = move(board_client, sunday.line[0], "espera-linha", **HTMX).content.decode()
        assert "Ninguém na fila pode ficar com esta vaga" in html
        assert entry(sunday.line[0]).status == EntryStatus.LISTA

    def test_line_player_to_the_goal(self, sunday: Sunday, board_client: Client) -> None:
        player = sunday.line[4]
        move(board_client, player, "lista-goleiro")
        assert entry(player).position == Position.GOLEIRO
        assert player in [row.player for row in board_roster(sunday.game_day).goalkeepers]

    def test_moving_after_the_saturday_deadline_is_never_fined(
        self,
        sunday: Sunday,
        board_client: Client,
        make_player: Callable[..., Player],
        clock: FixedClock,
    ) -> None:
        waiting = make_player("Esperando")
        games.join_list(sunday.game_day.pk, waiting)
        clock.set(SUNDAY_AFTER_DEADLINE)
        move(board_client, sunday.line[0], "espera-linha")
        assert entry(sunday.line[0]).status == EntryStatus.ESPERA
        assert entry(waiting).status == EntryStatus.LISTA
        assert not Fine.objects.exists()

    def test_charges_follow_who_holds_a_spot(
        self, sunday: Sunday, board_client: Client, make_player: Callable[..., Player], club_values
    ) -> None:
        waiting = make_player("Esperando")
        games.join_list(sunday.game_day.pk, waiting)
        move(board_client, waiting, "lista-linha", 1)
        end_game(sunday.game_day)
        charged = set(
            Charge.objects.filter(game_day=sunday.game_day).values_list("player", flat=True)
        )
        assert waiting.pk in charged
        assert sunday.line[-1].pk not in charged  # went to the queue to make room


class TestGuards:
    def test_only_the_board(self, sunday: Sunday, client: Client) -> None:
        client.force_login(sunday.line[0])
        response = move(client, sunday.line[0], "lista-linha", 1)
        assert response.status_code in {302, 403}
        assert entry(sunday.line[0]).board_rank is None

    def test_the_service_refuses_a_player_as_actor(self, sunday: Sunday) -> None:
        with pytest.raises(PermissionDenied):
            games.place_on_list(
                sunday.game_day.pk,
                sunday.line[0],
                Section(EntryStatus.LISTA, Position.LINHA),
                1,
                sunday.line[0],
            )

    def test_an_unknown_section_is_refused(self, sunday: Sunday, board_client: Client) -> None:
        html = move(board_client, sunday.line[0], "saiu-linha", **HTMX).content.decode()
        assert "Escolha um lugar válido na lista." in html

    def test_a_cancelled_sunday_cannot_be_edited(
        self, sunday: Sunday, board_client: Client, board_member: Player
    ) -> None:
        games.cancel_game_day(sunday.game_day.pk, board_member, "chuva")
        html = move(board_client, sunday.line[2], "lista-linha", 1, **HTMX).content.decode()
        assert "Este domingo foi cancelado pela diretoria." in html


class TestScreen:
    def test_each_row_has_its_moves(self, sunday: Sunday, board_client: Client) -> None:
        page = board_client.get(reverse("board:game_day", kwargs={"day": DAY})).content.decode()
        second = sunday.line[1].name
        assert f'aria-label="Subir {second}"' in page
        assert f'aria-label="Descer {second}"' in page
        assert f'aria-label="Mandar {second} para a fila"' in page
        assert f'aria-label="Passar {second} para o gol"' in page
        assert f'aria-label="Subir {sunday.line[0].name}"' not in page

    def test_a_placed_player_is_marked(self, sunday: Sunday, board_client: Client) -> None:
        move(board_client, sunday.line[3], "lista-linha", 1)
        page = board_client.get(reverse("board:game_day", kwargs={"day": DAY})).content.decode()
        assert "Lugar definido pela diretoria" in page
