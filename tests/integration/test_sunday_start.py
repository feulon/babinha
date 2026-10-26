"""The diretoria starts and ends the Sunday; the clock never does."""

import importlib
import random
from collections.abc import Callable
from datetime import datetime

import pytest
from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from games import services as games
from games.domain.roster import ListError
from games.models import Attendance, GameDay
from matchday import services as matchday
from matchday.domain.score import Score, Side
from matchday.models import LineupSpot, Match
from tests.conftest import SP, Sunday

pytestmark = pytest.mark.django_db

DAY = "2026-10-18"
SATURDAY = datetime(2026, 10, 17, 18, 0, tzinfo=SP)
EARLY_SUNDAY = datetime(2026, 10, 18, 9, 30, tzinfo=SP)
AFTER_10H = datetime(2026, 10, 18, 10, 30, tzinfo=SP)
START_URL = reverse("board:start", kwargs={"day": DAY})
SUNDAY_URL = reverse("board:game_day", kwargs={"day": DAY})


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday]) -> Sunday:
    return fill_sunday()


@pytest.fixture
def match(sunday: Sunday, board_member: Player) -> Match:
    matchday.draw(sunday.game_day.pk, board_member, random.Random(5))
    matchday.confirm(sunday.game_day.pk, board_member)
    return matchday.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    client.post(reverse("board:sunday_tutorial_seen"))
    return client


def text(response) -> str:
    return response.content.decode()


def match_url(match: Match) -> str:
    return reverse("board:match", kwargs={"match_id": match.pk})


def home_scorer(match: Match) -> Player:
    spot = LineupSpot.objects.filter(lineup=match.lineup, team=match.home_team).first()
    return spot.player


def started(game_day: GameDay) -> bool:
    game_day.refresh_from_db()
    return game_day.is_started


class TestStartService:
    def test_the_clock_never_starts_the_sunday(self, sunday: Sunday, clock: FixedClock) -> None:
        clock.set(AFTER_10H)
        assert not games.game_started(sunday.game_day)

    def test_the_diretoria_starts_it_before_10h_and_it_is_recorded(
        self, sunday: Sunday, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(EARLY_SUNDAY)
        assert games.start_game_day(sunday.game_day.pk, board_member) is True
        sunday.game_day.refresh_from_db()
        assert sunday.game_day.started_at == EARLY_SUNDAY
        assert sunday.game_day.started_by == board_member
        assert games.game_started(sunday.game_day)

    def test_starting_twice_keeps_the_first_start(
        self, sunday: Sunday, board_member: Player, president: Player, clock: FixedClock
    ) -> None:
        clock.set(EARLY_SUNDAY)
        games.start_game_day(sunday.game_day.pk, board_member)
        clock.set(AFTER_10H)
        assert games.start_game_day(sunday.game_day.pk, president) is False
        sunday.game_day.refresh_from_db()
        assert sunday.game_day.started_at == EARLY_SUNDAY
        assert sunday.game_day.started_by == board_member

    def test_only_the_diretoria_starts_it(self, sunday: Sunday, clock: FixedClock) -> None:
        clock.set(EARLY_SUNDAY)
        with pytest.raises(PermissionDenied):
            games.start_game_day(sunday.game_day.pk, sunday.line[0])
        assert not started(sunday.game_day)

    def test_not_before_the_day_of_the_game(
        self, sunday: Sunday, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(SATURDAY)
        assert not games.can_start(sunday.game_day)
        with pytest.raises(ListError, match="no dia do jogo"):
            games.start_game_day(sunday.game_day.pk, board_member)
        assert not started(sunday.game_day)

    def test_a_cancelled_sunday_cannot_start(
        self, sunday: Sunday, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(EARLY_SUNDAY)
        games.cancel_game_day(sunday.game_day.pk, board_member, "Chuva")
        sunday.game_day.refresh_from_db()
        assert not games.can_start(sunday.game_day)
        with pytest.raises(ListError, match="cancelado"):
            games.start_game_day(sunday.game_day.pk, board_member)

    def test_in_demo_mode_it_also_waits_for_the_diretoria(
        self, sunday: Sunday, board_member: Player, clock: FixedClock, settings
    ) -> None:
        settings.BABA_DEMO = True
        assert not games.game_started(sunday.game_day)
        assert games.can_start(sunday.game_day)  # the demo releases the calendar
        assert games.start_game_day(sunday.game_day.pk, board_member) is True

    def test_presence_waits_for_the_start_and_then_opens(
        self, sunday: Sunday, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(AFTER_10H)
        player = sunday.line[0]
        with pytest.raises(ListError, match="Iniciar o domingo"):
            games.mark_attendance(sunday.game_day.pk, player, Attendance.PRESENTE, board_member)
        games.start_game_day(sunday.game_day.pk, board_member)
        games.mark_attendance(sunday.game_day.pk, player, Attendance.PRESENTE, board_member)
        assert player.list_entries.get(game_day=sunday.game_day).attendance == Attendance.PRESENTE

    def test_only_the_diretoria_ends_it(
        self, sunday: Sunday, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(EARLY_SUNDAY)
        games.start_game_day(sunday.game_day.pk, board_member)
        clock.set(datetime(2026, 10, 19, 9, 0, tzinfo=SP))
        sunday.game_day.refresh_from_db()
        assert not games.sunday_over(sunday.game_day)
        assert games.finish_game_day(sunday.game_day.pk, board_member) is True
        sunday.game_day.refresh_from_db()
        assert games.sunday_over(sunday.game_day)


class TestGoalsFollowTheStart:
    def test_goals_assists_and_full_time_wait_for_the_start_not_the_clock(
        self, match: Match, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(AFTER_10H)
        scorer = home_scorer(match)
        with pytest.raises(matchday.MatchdayError, match="Iniciar o domingo"):
            matchday.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
        with pytest.raises(matchday.MatchdayError, match="Iniciar o domingo"):
            matchday.finish_match(match.pk, board_member)

    def test_right_after_the_start_they_count_even_before_10h(
        self, match: Match, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(EARLY_SUNDAY)
        games.start_game_day(match.lineup.game_day_id, board_member)
        scorer = home_scorer(match)
        mate = LineupSpot.objects.filter(lineup=match.lineup, team=match.home_team)[1].player
        goal = matchday.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=scorer.pk, assist=mate.pk
        )
        assert goal.assist == mate
        matchday.finish_match(match.pk, board_member)
        assert matchday.score_of(match) == Score(1, 0)


class TestMatchScreen:
    def test_before_the_start_it_asks_to_start_and_offers_no_goals_nor_timer(
        self, match: Match, board_client: Client, clock: FixedClock
    ) -> None:
        clock.set(EARLY_SUNDAY)
        page = text(board_client.get(match_url(match)))
        assert 'data-state="nao-iniciado"' in page
        assert "Inicie o domingo" in page
        assert f'action="{START_URL}"' in page
        assert f'name="next" value="{match_url(match)}"' in page
        assert "data-match-timer" not in page
        assert "Gol do " not in page
        assert "Encerrar partida" not in page

    def test_starting_from_the_match_comes_back_with_goals_and_the_timer(
        self, match: Match, board_client: Client, clock: FixedClock
    ) -> None:
        clock.set(EARLY_SUNDAY)
        response = board_client.post(START_URL, {"next": match_url(match)})
        assert response.status_code == 302
        assert response["Location"] == match_url(match)
        page = text(board_client.get(match_url(match)))
        assert "Domingo iniciado." in page
        assert 'data-state="nao-iniciado"' not in page
        assert "Inicie o domingo" not in page
        assert "data-match-timer" in page
        assert f"Gol do {match.home_team.name}" in page
        assert "Encerrar partida (fim do tempo)" in page

    def test_on_the_eve_there_is_nothing_to_start(
        self, match: Match, board_client: Client, clock: FixedClock
    ) -> None:
        clock.set(SATURDAY)
        page = text(board_client.get(match_url(match)))
        assert "Inicie o domingo" in page
        assert f'action="{START_URL}"' not in page
        assert "O domingo pode ser iniciado no dia do jogo." in page

    def test_a_foreign_next_falls_back_to_the_sunday_screen(
        self, sunday: Sunday, board_client: Client, clock: FixedClock
    ) -> None:
        clock.set(EARLY_SUNDAY)
        response = board_client.post(START_URL, {"next": "https://example.com/"})
        assert response["Location"] == SUNDAY_URL
        assert started(sunday.game_day)

    def test_a_player_cannot_start_it(self, sunday: Sunday, clock: FixedClock) -> None:
        clock.set(EARLY_SUNDAY)
        client = Client()
        client.force_login(sunday.line[0])
        assert client.post(START_URL).status_code != 302
        assert not started(sunday.game_day)


class TestSundayScreen:
    def test_the_presence_step_offers_the_start_and_opens_after_it(
        self, sunday: Sunday, board_client: Client, clock: FixedClock
    ) -> None:
        clock.set(AFTER_10H)
        page = text(board_client.get(SUNDAY_URL))
        assert "Abre quando o domingo começar" in page
        assert f'action="{START_URL}"' in page
        assert "Todos presentes" not in page
        response = board_client.post(START_URL, follow=True)
        page = text(response)
        assert "Domingo iniciado." in page
        assert f'action="{START_URL}"' not in page
        assert "Todos presentes" in page
        assert "Cobranças abrem ao finalizar o jogo" in page


class TestMigration:
    def test_sundays_already_under_way_keep_their_start(
        self, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        migration = importlib.import_module("games.migrations.0005_game_day_started")
        played = GameDay.objects.create(date=datetime(2026, 10, 11).date())
        today = GameDay.objects.create(date=datetime(2026, 10, 18).date())
        clock.set(datetime(2026, 10, 18, 9, 0, tzinfo=SP))
        monkeypatch.setattr(migration.timezone, "now", clock.now)
        migration.start_the_sundays_already_under_way(apps, None)
        played.refresh_from_db()
        today.refresh_from_db()
        assert played.started_at == datetime(2026, 10, 11, 10, 0, tzinfo=SP)
        assert today.started_at is None
