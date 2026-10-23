from collections.abc import Callable
from datetime import datetime

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player, PlayerType
from core.clock import FixedClock
from core.models import ClubSettings
from games import services
from games.models import Attendance, GameDay, ListEntry
from games.promotion import promote_agregados
from tests.conftest import SP, TEST_PASSWORD

pytestmark = pytest.mark.django_db

SEPTEMBER = [datetime(2026, 9, d, 12, 31, tzinfo=SP) for d in (6, 13, 20, 27)]
SUNDAYS = [datetime(2026, 10, d, 12, 31, tzinfo=SP) for d in (4, 11, 18, 25)] + [
    datetime(2026, 11, d, 12, 31, tzinfo=SP) for d in (1, 8)
]


def play(player: Player, count: int, first: int = 0, sundays: list = SUNDAYS) -> None:
    """Give `player` a present entry on `count` finished Sundays, starting at SUNDAYS[first]."""
    for when in sundays[first : first + count]:
        day, _ = GameDay.objects.get_or_create(date=when.date(), defaults={"finished_at": when})
        ListEntry.objects.create(
            game_day=day,
            player=player,
            position="linha",
            status="lista",
            joined_at=when,
            confirmed_at=when,
            attendance=Attendance.PRESENTE,
        )


def refreshed(player: Player) -> str:
    player.refresh_from_db()
    return player.player_type


class TestPromotion:
    def test_agregado_with_five_games_becomes_avulso(self, make_player: Callable) -> None:
        agregado = make_player(player_type=PlayerType.AGREGADO)
        play(agregado, 5)
        assert promote_agregados() == [agregado]
        assert refreshed(agregado) == PlayerType.AVULSO

    def test_four_games_are_not_enough(self, make_player: Callable) -> None:
        agregado = make_player(player_type=PlayerType.AGREGADO)
        play(agregado, 4)
        assert promote_agregados() == []
        assert refreshed(agregado) == PlayerType.AGREGADO

    def test_absences_and_unfinished_sundays_do_not_count(self, make_player: Callable) -> None:
        agregado = make_player(player_type=PlayerType.AGREGADO)
        play(agregado, 4)
        ListEntry.objects.filter(player=agregado).update(attendance=Attendance.FALTOU)
        play(agregado, 2, first=4)
        GameDay.objects.filter(date=SUNDAYS[5].date()).update(finished_at=None)
        assert promote_agregados() == []

    def test_running_twice_changes_nothing(self, make_player: Callable) -> None:
        agregado = make_player(player_type=PlayerType.AGREGADO)
        play(agregado, 5)
        promote_agregados()
        stamp = Player.objects.get(pk=agregado.pk).player_type_set_at
        assert promote_agregados() == []
        assert Player.objects.get(pk=agregado.pk).player_type_set_at == stamp

    def test_avulso_is_not_touched(self, make_player: Callable) -> None:
        avulso = make_player(player_type=PlayerType.AVULSO)
        play(avulso, 1)
        promote_agregados()
        assert refreshed(avulso) == PlayerType.AVULSO

    def test_mensalista_keeps_enrollment_and_type(self, make_player: Callable, clock) -> None:
        mensalista = make_player(player_type=PlayerType.MENSALISTA)
        agregado = make_player(player_type=PlayerType.AGREGADO)
        play(agregado, 5)
        play(mensalista, 5)
        promote_agregados()
        mensalista.refresh_from_db()
        assert mensalista.is_mensalista
        assert mensalista.player_type == PlayerType.AVULSO
        assert mensalista.player_type_set_at is None

    def test_limit_comes_from_club_settings(self, make_player: Callable) -> None:
        ClubSettings.objects.update_or_create(pk=1, defaults={"avulso_after_games": 2})
        agregado = make_player(player_type=PlayerType.AGREGADO)
        play(agregado, 2)
        promote_agregados()
        assert refreshed(agregado) == PlayerType.AVULSO

    def test_finishing_the_sunday_promotes(
        self, make_player: Callable, board_member: Player, club_values, clock: FixedClock
    ) -> None:
        agregado = make_player(player_type=PlayerType.AGREGADO)
        play(agregado, 4, sundays=SEPTEMBER)
        day = services.current_game_day()
        ListEntry.objects.create(
            game_day=day,
            player=agregado,
            position="linha",
            status="lista",
            joined_at=clock.now(),
            confirmed_at=clock.now(),
            attendance=Attendance.PRESENTE,
        )
        clock.set(datetime(2026, 10, 18, 10, 30, tzinfo=SP))
        services.finish_game_day(day.pk, board_member)
        assert refreshed(agregado) == PlayerType.AVULSO

    def test_board_putting_agregado_back_waits_for_new_games(
        self, make_player: Callable, board_member: Player, clock: FixedClock
    ) -> None:
        agregado = make_player(player_type=PlayerType.AGREGADO)
        play(agregado, 5)
        promote_agregados()
        client = Client()
        client.force_login(board_member)
        clock.set(datetime(2026, 11, 9, 9, 0, tzinfo=SP))
        response = client.post(
            reverse("board:player_edit", args=[agregado.pk]),
            {
                "name": agregado.name,
                "phone": agregado.phone,
                "email": agregado.email,
                "player_type": PlayerType.AGREGADO,
                "position": agregado.position,
                "pote": agregado.pote,
                "referred_by": agregado.referred_by_id or board_member.pk,
                "is_active": "on",
            },
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        assert refreshed(agregado) == PlayerType.AGREGADO
        assert promote_agregados() == []
        assert refreshed(agregado) == PlayerType.AGREGADO

    def test_board_can_force_avulso(
        self, make_player: Callable, board_member: Player, clock: FixedClock
    ) -> None:
        agregado = make_player(player_type=PlayerType.AGREGADO)
        client = Client()
        client.force_login(board_member)
        response = client.post(
            reverse("board:player_edit", args=[agregado.pk]),
            {
                "name": agregado.name,
                "phone": agregado.phone,
                "email": agregado.email,
                "player_type": PlayerType.AVULSO,
                "position": agregado.position,
                "pote": agregado.pote,
                "referred_by": agregado.referred_by_id or board_member.pk,
                "is_active": "on",
            },
        )
        assert response.status_code == 302
        assert refreshed(agregado) == PlayerType.AVULSO


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


class TestSettingsField:
    def test_default_is_five(self, db: None) -> None:
        assert ClubSettings.load().avulso_after_games == 5

    def test_minimum_is_one(self, board_client: Client) -> None:
        data = {
            "monthly_fee": "100,00",
            "single_fee": "30,00",
            "fine_base": "10,00",
            "fine_factor": "1,50",
            "late_fine_15": "10,00",
            "late_fine_30": "25,00",
            "monthly_due_day": "10",
            "pix_key": "",
            "board_contact_phone": "",
            "venue_name": "",
        }
        bad = board_client.post(reverse("board:settings"), {**data, "avulso_after_games": "0"})
        assert bad.status_code == 200
        ok = board_client.post(reverse("board:settings"), {**data, "avulso_after_games": "3"})
        assert ok.status_code == 302
        assert ClubSettings.load().avulso_after_games == 3


class TestFirstLogin:
    def sign_in(self, client: Client, player: Player, **extra: str):
        return client.post(
            reverse("accounts:login"),
            {"login": player.phone, "password": TEST_PASSWORD, **extra},
        )

    def test_first_login_goes_to_rules_once(self, make_player: Callable) -> None:
        player = make_player(rules_seen_at=None)
        client = Client()
        response = self.sign_in(client, player, next="/jogo/")
        assert response["Location"] == reverse("rules")
        player.refresh_from_db()
        assert player.rules_seen_at is not None
        client.logout()
        response = self.sign_in(client, player)
        assert response["Location"] == reverse("games:home")

    def test_next_is_respected_after_the_first_login(self, make_player: Callable) -> None:
        player = make_player()
        response = self.sign_in(Client(), player, next=reverse("rules"))
        assert response["Location"] == reverse("rules")
        response = self.sign_in(Client(), player, next="/mais/")
        assert response["Location"] == "/mais/"
