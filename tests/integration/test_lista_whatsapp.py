"""manage.py lista_whatsapp: the Sunday list as plain text, in the app's own order."""

from collections.abc import Callable
from datetime import date, datetime
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from accounts.models import Player, PlayerType, Position
from core.clock import FixedClock
from core.models import ClubSettings
from games import services as games
from games.domain.roster import Section
from games.domain.types import EntryStatus
from games.models import GameDay
from tests.conftest import SP, Sunday

pytestmark = pytest.mark.django_db

SUNDAY_DATE = date(2026, 10, 18)


def lista(*args: str) -> str:
    out = StringIO()
    call_command("lista_whatsapp", *args, stdout=out)
    return out.getvalue()


def join(make_player: Callable[..., Player], president: Player, name: str, **extra) -> Player:
    player = make_player(name, referred_by=president, **extra)
    games.join_list(games.current_game_day().pk, player)
    return player


def test_lists_every_section_in_the_apps_order(
    clock: FixedClock, make_player: Callable[..., Player], president: Player
) -> None:
    ClubSettings.objects.update_or_create(pk=1, defaults={"venue_name": "Arena Ibirapuera"})
    join(make_player, president, "Matheus", position=Position.GOLEIRO)
    join(make_player, president, "Sonnesso", position=Position.GOLEIRO)
    join(make_player, president, "Gui Hajjar", player_type=PlayerType.AVULSO)
    join(make_player, president, "Bruno B", player_type=PlayerType.MENSALISTA)
    join(make_player, president, "Lollato", player_type=PlayerType.MENSALISTA)
    clock.set(datetime(2026, 10, 14, 21, 0, tzinfo=SP))  # Wednesday 21h: agregados may sign up
    join(make_player, president, "AA", player_type=PlayerType.AGREGADO)

    assert lista() == (
        "Domingo (18/10)\n"
        "10h00 | Arena Ibirapuera\n"
        "\n"
        "GOLEIROS 🧤\n"
        "1. Matheus 🧤\n"
        "2. Sonnesso 🧤\n"
        "\n"
        "Mensalistas ⚽️\n"
        "1. Bruno B\n"
        "2. Lollato\n"
        "\n"
        "AVULSOS\n"
        "1. Gui Hajjar\n"
        "\n"
        "AGREGADOS 🐣\n"
        "1. AA\n"
    )


def test_players_are_shown_by_nickname(
    clock: FixedClock, make_player: Callable[..., Player], president: Player
) -> None:
    join(make_player, president, "Carlos Eduardo da Silva", nickname="Cadu")
    out = lista()
    assert "1. Cadu\n" in out
    assert "Carlos" not in out


def test_follows_the_order_the_board_gave_the_list(
    clock: FixedClock, make_player: Callable[..., Player], president: Player
) -> None:
    join(make_player, president, "Primeiro")
    join(make_player, president, "Segundo")
    third = join(make_player, president, "Terceiro")
    assert lista().index("Primeiro") < lista().index("Segundo") < lista().index("Terceiro")
    section = Section(EntryStatus.LISTA, Position.LINHA)
    games.place_on_list(games.current_game_day().pk, third, section, 1, president)
    assert lista().index("Terceiro") < lista().index("Primeiro") < lista().index("Segundo")


def test_waitlist_is_its_own_section_after_the_rest(
    clock: FixedClock, fill_sunday: Callable[..., Sunday], make_player, president: Player
) -> None:
    fill_sunday(line=22, goalkeepers=0)
    join(make_player, president, "Esperando 1")
    join(make_player, president, "Esperando 2")
    out = lista()
    assert out.endswith("ESPERA\n1. Esperando 1\n2. Esperando 2\n")
    assert out.index("AVULSOS") < out.index("ESPERA")


def test_a_waiting_goalkeeper_is_marked(
    clock: FixedClock, fill_sunday: Callable[..., Sunday], make_player, president: Player
) -> None:
    fill_sunday(line=20, goalkeepers=4)
    join(make_player, president, "Quinto Goleiro", position=Position.GOLEIRO)
    assert lista().endswith("ESPERA\n1. Quinto Goleiro 🧤\n")


def test_empty_sections_are_left_out_and_there_are_no_blank_slots(
    clock: FixedClock, make_player: Callable[..., Player], president: Player
) -> None:
    join(make_player, president, "Sozinho")
    out = lista()
    assert "GOLEIROS" not in out
    assert "Mensalistas" not in out
    assert "AGREGADOS" not in out
    assert "ESPERA" not in out
    assert out == "Domingo (18/10)\n10h00\n\nAVULSOS\n1. Sozinho\n"


def test_an_empty_list_says_so(clock: FixedClock) -> None:
    assert lista() == "Domingo (18/10)\n10h00\n\nNinguém na lista ainda.\n"
    assert not GameDay.objects.exists()  # reading does not create the Sunday


def test_people_who_left_are_not_listed(
    clock: FixedClock, make_player: Callable[..., Player], president: Player
) -> None:
    gone = join(make_player, president, "Desistiu")
    join(make_player, president, "Ficou")
    games.leave_list(games.current_game_day().pk, gone)
    out = lista()
    assert "Desistiu" not in out
    assert "1. Ficou" in out


def test_data_picks_another_sunday(
    clock: FixedClock, make_player: Callable[..., Player], president: Player
) -> None:
    join(make_player, president, "Deste domingo")
    assert lista("--data", "25/10/2026") == "Domingo (25/10)\n10h00\n\nNinguém na lista ainda.\n"
    assert "Deste domingo" in lista("--data", "18/10/2026")


def test_the_next_sunday_comes_after_the_game_ended(
    clock: FixedClock, make_player: Callable[..., Player], president: Player
) -> None:
    join(make_player, president, "Deste domingo")
    clock.set(datetime(2026, 10, 18, 13, 0, tzinfo=SP))
    assert lista().startswith("Domingo (25/10)")


@pytest.mark.parametrize("raw", ["18-10-2026", "amanhã", "31/02/2026"])
def test_a_bad_date_is_refused(raw: str) -> None:
    with pytest.raises(CommandError, match="Data inválida"):
        lista("--data", raw)


def test_a_date_that_is_not_a_sunday_is_refused() -> None:
    with pytest.raises(CommandError, match="não é um domingo"):
        lista("--data", "19/10/2026")
