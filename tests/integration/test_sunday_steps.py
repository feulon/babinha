"""The diretoria's Sunday screen: who can still be added, and the no-show fines of the last step."""

import random
from collections.abc import Callable
from datetime import datetime

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from fines.models import FineReason
from fines.services import record_fine
from games import services as games
from games.models import Attendance
from matchday import services
from matchday.models import Lineup
from tests.conftest import SP, Sunday

pytestmark = pytest.mark.django_db

DAY = "2026-10-18"
AFTER_KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)
URL = reverse("board:game_day", kwargs={"day": DAY})


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday], club_values) -> Sunday:
    return fill_sunday()


def page(client: Client) -> str:
    return client.get(URL).content.decode()


def draw(sunday: Sunday, board_member: Player) -> Lineup:
    return services.draw(sunday.game_day.pk, board_member, random.Random(5))


class TestBeforeTheTeams:
    def test_the_list_adds_a_player_only_before_the_game_starts(
        self, sunday: Sunday, board_client: Client, clock: FixedClock
    ) -> None:
        assert reverse("board:add_to_list", kwargs={"day": DAY}) in page(board_client)
        clock.set(AFTER_KICKOFF)
        assert reverse("board:add_to_list", kwargs={"day": DAY}) not in page(board_client)


class TestAfterTheTeams:
    @pytest.fixture
    def confirmed(self, sunday: Sunday, board_member: Player) -> Lineup:
        lineup = draw(sunday, board_member)
        services.confirm(sunday.game_day.pk, board_member)
        return lineup

    def test_a_confirmed_table_does_not_depend_on_attendance(
        self, sunday: Sunday, confirmed: Lineup, board_client: Client, clock: FixedClock
    ) -> None:
        """Nobody has been marked, and the games screen still lets the first game start."""
        response = board_client.post(
            reverse("board:start_next_match", kwargs={"day": DAY}), follow=True
        )
        assert "Time A x Time B em campo." in response.content.decode()


class TestAttendanceStep:
    @pytest.fixture
    def started(self, sunday: Sunday, board_member: Player, clock: FixedClock) -> Lineup:
        lineup = draw(sunday, board_member)
        services.confirm(sunday.game_day.pk, board_member)
        clock.set(AFTER_KICKOFF)
        return lineup

    def test_a_no_show_only_records_the_fine_and_keeps_the_confirmed_teams(
        self, sunday: Sunday, started: Lineup, board_member: Player
    ) -> None:
        player = sunday.line[0]
        spots = set(started.spots.values_list("pk", "player_id", "team_id"))
        fine = games.mark_attendance(sunday.game_day.pk, player, Attendance.FALTOU, board_member)
        assert fine is not None
        assert fine.player == player
        started.refresh_from_db()
        assert started.is_confirmed
        assert set(started.spots.values_list("pk", "player_id", "team_id")) == spots

    def test_a_no_show_is_listed_in_the_last_step_with_its_own_fine(
        self, sunday: Sunday, started: Lineup, board_client: Client, board_member: Player
    ) -> None:
        absent, late = sunday.line[0], sunday.line[1]
        games.mark_attendance(sunday.game_day.pk, absent, Attendance.FALTOU, board_member)
        games.mark_late(sunday.game_day.pk, late, 15, board_member)
        step = page(board_client)
        step = step[step.index('id="passo-fim"') :]
        no_show = step[step.index('id="multar-falta"') : step.index('id="multar-atraso"')]
        assert absent.name in no_show
        assert "Falta sem aviso" in no_show
        assert "R$ 10,00" in no_show
        assert "1ª ocorrência" in no_show
        assert late.name not in no_show
        late_block = step[step.index('id="multar-atraso"') :]
        assert late.name in late_block
        assert absent.name not in late_block
        assert "Multar por atraso" in late_block
        assert "Falta sem aviso" not in late_block

    def test_the_no_show_keeps_the_escalated_value_in_the_last_step(
        self, sunday: Sunday, started: Lineup, board_client: Client, board_member: Player
    ) -> None:
        repeat = sunday.line[0]
        record_fine(repeat, sunday.game_day, FineReason.LATE_DROPOUT)
        games.mark_attendance(sunday.game_day.pk, repeat, Attendance.FALTOU, board_member)
        step = page(board_client)
        no_show = step[step.index('id="multar-falta"') :]
        assert "2ª ocorrência" in no_show
        assert "R$ 15,00" in no_show

    def test_a_corrected_attendance_leaves_the_no_show_list(
        self, sunday: Sunday, started: Lineup, board_client: Client, board_member: Player
    ) -> None:
        absent = sunday.line[0]
        games.mark_attendance(sunday.game_day.pk, absent, Attendance.FALTOU, board_member)
        games.mark_attendance(sunday.game_day.pk, absent, Attendance.PRESENTE, board_member)
        assert 'id="multar-falta"' not in page(board_client)
