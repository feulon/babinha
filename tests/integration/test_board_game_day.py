"""Game day for the diretoria: attendance, start rule, auto reopen, queue and goals."""

import random
from collections.abc import Callable
from datetime import datetime

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player, PlayerType
from core.clock import FixedClock
from fines.models import Fine
from games import services as games
from games.models import Attendance, GameDay, ListEntry
from matchday import services
from matchday.domain.score import Score, Side
from matchday.models import Goal, Lineup, LineupSpot, Match
from tests.conftest import SP, Sunday, start_game

pytestmark = pytest.mark.django_db

DAY = "2026-10-18"
BEFORE_KICKOFF = datetime(2026, 10, 18, 9, 55, tzinfo=SP)
AFTER_KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)
MESSAGE_NOT_STARTED = "O domingo ainda não começou. Toque em Iniciar o domingo."


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday]) -> Sunday:
    return fill_sunday()


@pytest.fixture
def lineup(sunday: Sunday, board_member: Player) -> Lineup:
    return services.draw(sunday.game_day.pk, board_member, random.Random(5))


@pytest.fixture
def match(sunday: Sunday, lineup: Lineup, board_member: Player, clock: FixedClock) -> Match:
    return services.add_match(
        sunday.game_day.pk,
        board_member,
        home_team=1,
        away_team=2,
        home_goalkeeper=sunday.goalkeepers[0].pk,
        away_goalkeeper=sunday.goalkeepers[1].pk,
    )


def text(response) -> str:
    return response.content.decode()


def team_players(lineup: Lineup, number: int) -> list[Player]:
    spots = LineupSpot.objects.filter(lineup=lineup, team__number=number).select_related("player")
    return [spot.player for spot in spots.order_by("pk")]


def goal(client: Client, match: Match, side: str, **data: object) -> str:
    url = reverse("board:add_goal", kwargs={"match_id": match.pk, "side": side})
    return text(client.post(url, data, follow=True))


class TestAttendanceScreen:
    def test_absent_button_asks_before_fining_and_names_the_amount(
        self, sunday: Sunday, board_client: Client, clock: FixedClock, club_values
    ) -> None:
        clock.set(AFTER_KICKOFF)
        start_game()
        html = text(board_client.get(reverse("board:game_day", kwargs={"day": DAY})))
        assert (
            'hx-confirm="Marcar Linha 01 como faltou? Gera multa de R$ 10,00 (1ª ocorrência)."'
            in html
        )

    def test_the_amount_follows_the_players_earlier_fines(
        self,
        sunday: Sunday,
        board_member: Player,
        board_client: Client,
        clock: FixedClock,
        club_values,
    ) -> None:
        clock.set(AFTER_KICKOFF)
        start_game()
        repeat = sunday.line[0]
        games.mark_attendance(sunday.game_day.pk, repeat, Attendance.FALTOU, board_member)
        games.mark_attendance(sunday.game_day.pk, repeat, Attendance.PRESENTE, board_member)
        Fine.objects.create(
            player=repeat,
            game_day=sunday.game_day,
            reason="desistencia",
            ordinal=1,
            amount=10,
            created_at=clock.now(),
        )
        html = text(board_client.get(reverse("board:game_day", kwargs={"day": DAY})))
        assert "Marcar Linha 01 como faltou? Gera multa de R$ 15,00 (2ª ocorrência)." in html


class TestAllPresent:
    def url(self) -> str:
        return reverse("board:mark_all_present", kwargs={"day": DAY})

    def test_marks_everyone_without_a_mark_and_keeps_the_absent(
        self, sunday: Sunday, board_member: Player, board_client: Client, clock: FixedClock
    ) -> None:
        clock.set(AFTER_KICKOFF)
        start_game()
        absent = sunday.line[0]
        games.mark_attendance(sunday.game_day.pk, absent, Attendance.FALTOU, board_member)
        response = board_client.post(self.url(), follow=True)
        assert "21 jogadores marcados como presentes." in text(response)
        marks = dict(
            ListEntry.objects.filter(game_day=sunday.game_day).values_list(
                "player_id", "attendance"
            )
        )
        assert marks[absent.pk] == Attendance.FALTOU
        assert [m for pk, m in marks.items() if pk != absent.pk] == [Attendance.PRESENTE] * 21
        assert Fine.objects.count() == 1

    def test_records_who_marked_and_when(
        self, sunday: Sunday, board_member: Player, board_client: Client, clock: FixedClock
    ) -> None:
        clock.set(AFTER_KICKOFF)
        start_game()
        board_client.post(self.url())
        entry = ListEntry.objects.get(game_day=sunday.game_day, player=sunday.line[0])
        assert entry.attendance_marked_by == board_member
        assert entry.attendance_marked_at == AFTER_KICKOFF

    def test_a_second_tap_has_nothing_left_to_mark(
        self, sunday: Sunday, board_client: Client, clock: FixedClock
    ) -> None:
        clock.set(AFTER_KICKOFF)
        start_game()
        board_client.post(self.url())
        response = board_client.post(self.url(), follow=True)
        assert "Todos da lista já estão com a presença marcada." in text(response)

    def test_not_before_the_diretoria_starts_even_after_10h(
        self, sunday: Sunday, board_client: Client, clock: FixedClock
    ) -> None:
        clock.set(AFTER_KICKOFF)
        response = board_client.post(self.url(), follow=True)
        assert MESSAGE_NOT_STARTED in text(response)
        assert not ListEntry.objects.filter(game_day=sunday.game_day).exclude(attendance="")

    def test_only_the_board(self, sunday: Sunday, clock: FixedClock) -> None:
        clock.set(AFTER_KICKOFF)
        start_game()
        client = Client()
        client.force_login(sunday.line[0])
        assert client.post(self.url()).status_code != 200
        assert not ListEntry.objects.filter(game_day=sunday.game_day).exclude(attendance="")


class TestBumpMessage:
    def test_names_who_went_to_the_waitlist(
        self, sunday: Sunday, make_player, board_client: Client
    ) -> None:
        mensalista = make_player("Mensal", player_type=PlayerType.MENSALISTA)
        response = board_client.post(
            reverse("board:add_to_list", kwargs={"day": DAY}),
            {"player": mensalista.pk},
            follow=True,
        )
        html = text(response)
        assert "Mensal entrou na lista." in html
        assert "Linha 20 (o último a entrar) foi para a espera por prioridade." in html


class TestStartRule:
    """Goals and full time wait for the diretoria to start the Sunday, never for the clock."""

    def test_no_goal_until_the_diretoria_starts_even_after_10h(
        self, match: Match, lineup: Lineup, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(AFTER_KICKOFF)
        scorer = team_players(lineup, 1)[0]
        with pytest.raises(services.MatchdayError, match=MESSAGE_NOT_STARTED):
            services.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
        assert not Goal.objects.exists()

    def test_no_full_time_until_the_diretoria_starts(
        self, match: Match, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(AFTER_KICKOFF)
        with pytest.raises(services.MatchdayError, match=MESSAGE_NOT_STARTED):
            services.finish_match(match.pk, board_member)
        match.refresh_from_db()
        assert not match.is_finished

    def test_no_goal_correction_without_a_start(
        self, match: Match, lineup: Lineup, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(BEFORE_KICKOFF)
        start_game()
        home = team_players(lineup, 1)
        recorded = services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[0].pk)
        GameDay.objects.filter(pk=match.lineup.game_day_id).update(started_at=None)
        with pytest.raises(services.MatchdayError, match=MESSAGE_NOT_STARTED):
            services.edit_goal(recorded.pk, board_member, side=Side.HOME, scorer=home[1].pk)

    def test_goals_assists_and_full_time_open_as_soon_as_the_diretoria_starts_before_10h(
        self, match: Match, lineup: Lineup, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(BEFORE_KICKOFF)
        assert games.start_game_day(match.lineup.game_day_id, board_member) is True
        home = team_players(lineup, 1)
        services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=home[0].pk, assist=home[1].pk
        )
        assert services.score_of(match) == Score(1, 0)
        assert Goal.objects.get().assist == home[1]
        services.finish_match(match.pk, board_member)
        match.refresh_from_db()
        assert match.is_finished

    def test_the_view_flashes_the_error(
        self, match: Match, lineup: Lineup, board_client: Client, clock: FixedClock
    ) -> None:
        clock.set(AFTER_KICKOFF)
        scorer = team_players(lineup, 1)[0]
        assert MESSAGE_NOT_STARTED in goal(board_client, match, "home", scorer=scorer.pk)


class TestDeletingTheDecidingGoal:
    @pytest.fixture
    def decided(
        self, match: Match, lineup: Lineup, board_member: Player, clock: FixedClock
    ) -> tuple[Match, list[Goal]]:
        clock.set(AFTER_KICKOFF)
        start_game()
        home, away = team_players(lineup, 1), team_players(lineup, 2)
        goals = [
            services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[0].pk),
            services.add_goal(match.pk, board_member, side=Side.AWAY, scorer=away[0].pk),
            services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[1].pk),
        ]
        match.refresh_from_db()
        assert match.is_finished
        return match, goals

    def test_the_match_goes_back_to_play(
        self, decided: tuple[Match, list[Goal]], board_member: Player
    ) -> None:
        match, goals = decided
        assert services.delete_goal(goals[2].pk, board_member) is True
        match.refresh_from_db()
        assert not match.is_finished
        assert match.finished_by is None
        assert services.score_of(match) == Score(1, 1)

    def test_an_earlier_goal_does_not_reopen_a_decided_match(
        self, decided: tuple[Match, list[Goal]], board_member: Player
    ) -> None:
        match, goals = decided
        assert services.delete_goal(goals[1].pk, board_member) is False  # 2 x 0 is still decided
        match.refresh_from_db()
        assert match.is_finished

    def test_a_match_closed_at_full_time_stays_closed(
        self, match: Match, lineup: Lineup, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(AFTER_KICKOFF)
        start_game()
        scored = services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=team_players(lineup, 1)[0].pk
        )
        services.finish_match(match.pk, board_member)
        assert services.delete_goal(scored.pk, board_member) is False
        match.refresh_from_db()
        assert match.is_finished

    def test_moving_a_goal_to_the_other_team_reopens_when_it_undoes_the_win(
        self, match: Match, lineup: Lineup, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(AFTER_KICKOFF)
        start_game()
        home = team_players(lineup, 1)
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[0].pk)
        second = services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[1].pk)
        match.refresh_from_db()
        assert match.is_finished
        services.edit_goal(second.pk, board_member, side=Side.AWAY, scorer=home[2].pk)
        match.refresh_from_db()
        assert not match.is_finished
        assert services.score_of(match) == Score(1, 1)


class TestStartNextMatch:
    def url(self) -> str:
        return reverse("board:start_next_match", kwargs={"day": DAY})

    def play(self, board_member: Player, home: int, away: int) -> Match:
        """Start the next match and take it to the score, closing it at full time if undecided."""
        created = services.start_next_match(Lineup.objects.get().game_day_id, board_member)
        # The side with fewer goals scores first: two goals end the match.
        for side, goals in sorted(((Side.HOME, home), (Side.AWAY, away)), key=lambda p: p[1]):
            for n in range(goals):
                scorer = services.side_scorers(created, side)[n]
                services.add_goal(created.pk, board_member, side=side, scorer=scorer.pk)
        created.refresh_from_db()
        if not created.is_finished:
            services.finish_match(created.pk, board_member)
        created.refresh_from_db()
        return created

    def teams_of(self, match: Match) -> tuple[int, int]:
        return match.home_team.number, match.away_team.number

    def test_starting_opens_the_first_match_between_the_first_two_teams(
        self, sunday: Sunday, lineup: Lineup, board_client: Client
    ) -> None:
        response = board_client.post(self.url(), follow=True)
        created = Match.objects.get()
        assert self.teams_of(created) == (1, 2)
        assert response.redirect_chain[-1][0] == reverse(
            "board:match", kwargs={"match_id": created.pk}
        )
        assert "Time A x Time B em campo." in text(response)

    def test_players_follow_the_teams_like_any_added_match(
        self, sunday: Sunday, lineup: Lineup, board_client: Client
    ) -> None:
        board_client.post(self.url())
        expected = len(team_players(lineup, 1)) + len(team_players(lineup, 2))
        assert Match.objects.get().players.count() == expected

    def test_the_next_match_waits_for_the_one_on_court(
        self, sunday: Sunday, lineup: Lineup, board_client: Client
    ) -> None:
        board_client.post(self.url())
        response = board_client.post(self.url(), follow=True)
        assert "Encerre o jogo em andamento antes de chamar o próximo." in text(response)
        assert Match.objects.count() == 1

    def test_the_winner_stays_and_the_team_waiting_longest_comes_in(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, after_kickoff: FixedClock
    ) -> None:
        self.play(board_member, 2, 0)
        assert self.teams_of(self.play(board_member, 2, 1)) == (1, 3)
        assert self.teams_of(self.play(board_member, 0, 2)) == (1, 4)
        assert self.teams_of(services.start_next_match(sunday.game_day.pk, board_member)) == (4, 2)

    def test_a_draw_at_full_time_sends_both_teams_out(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, after_kickoff: FixedClock
    ) -> None:
        first = self.play(board_member, 1, 1)
        assert first.is_finished
        assert self.teams_of(services.start_next_match(sunday.game_day.pk, board_member)) == (3, 4)

    def test_after_the_draw_the_two_who_sat_out_are_next_and_then_the_first_pair_again(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, after_kickoff: FixedClock
    ) -> None:
        self.play(board_member, 0, 0)
        self.play(board_member, 1, 1)
        assert self.teams_of(services.start_next_match(sunday.game_day.pk, board_member)) == (1, 2)

    def test_goalkeepers_rotate_and_the_winners_keeper_stays(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, after_kickoff: FixedClock
    ) -> None:
        first_keeper, second_keeper = sunday.goalkeepers
        first = self.play(board_member, 2, 0)
        assert (first.home_goalkeeper, first.away_goalkeeper) == (first_keeper, second_keeper)
        second = services.start_next_match(sunday.game_day.pk, board_member)
        assert (second.home_goalkeeper, second.away_goalkeeper) == (first_keeper, second_keeper)

    def test_works_without_goalkeepers_leaving_the_seats_open(
        self, fill_sunday: Callable[..., Sunday], board_member: Player, board_client: Client
    ) -> None:
        sunday = fill_sunday(line=20, goalkeepers=0)
        services.draw(sunday.game_day.pk, board_member, random.Random(5))
        board_client.post(self.url())
        assert Match.objects.count() == 1
        assert not Match.objects.exclude(home_goalkeeper=None, away_goalkeeper=None).exists()

    def test_needs_the_teams_drawn(self, sunday: Sunday, board_client: Client) -> None:
        response = board_client.post(self.url(), follow=True)
        assert "Sorteie os times primeiro." in text(response)
        assert not Match.objects.exists()

    def test_a_cancelled_sunday_starts_no_match(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, board_client: Client
    ) -> None:
        games.cancel_game_day(sunday.game_day.pk, board_member, "Chuva")
        response = board_client.post(self.url(), follow=True)
        assert "Este domingo foi cancelado." in text(response)
        assert not Match.objects.exists()

    def test_a_finished_game_starts_no_match(
        self,
        sunday: Sunday,
        lineup: Lineup,
        board_member: Player,
        board_client: Client,
        after_kickoff: FixedClock,
    ) -> None:
        games.finish_game_day(sunday.game_day.pk, board_member)
        response = board_client.post(self.url(), follow=True)
        assert "O jogo deste domingo já foi finalizado." in text(response)
        assert not Match.objects.exists()

    def test_only_the_board(self, sunday: Sunday, lineup: Lineup) -> None:
        client = Client()
        client.force_login(sunday.line[0])
        assert client.post(self.url()).status_code != 200
        assert not Match.objects.exists()


class TestQuickGoal:
    @pytest.fixture(autouse=True)
    def started(self, match: Match, clock: FixedClock) -> None:
        clock.set(AFTER_KICKOFF)
        start_game()

    def test_one_tap_records_the_goal(
        self, match: Match, lineup: Lineup, board_client: Client
    ) -> None:
        scorer = team_players(lineup, 1)[0]
        assert "Gol do Time A!" in goal(board_client, match, "home", scorer=scorer.pk, assist="")
        recorded = Goal.objects.get()
        assert (recorded.scorer, recorded.assist, recorded.team) == (scorer, None, match.home_team)

    def test_a_player_can_be_the_assist(
        self, match: Match, lineup: Lineup, board_client: Client
    ) -> None:
        home = team_players(lineup, 1)
        goal(board_client, match, "home", scorer=home[0].pk, assist=home[1].pk)
        recorded = Goal.objects.get()
        assert (recorded.assist, recorded.assist_not_applicable) == (home[1], False)

    def test_nobody_gave_the_assist(
        self, match: Match, lineup: Lineup, board_client: Client
    ) -> None:
        goal(board_client, match, "home", scorer=team_players(lineup, 1)[0].pk, assist="")
        recorded = Goal.objects.get()
        assert (recorded.assist, recorded.assist_not_applicable) == (None, False)

    def test_the_assist_may_not_apply(
        self, match: Match, lineup: Lineup, board_client: Client
    ) -> None:
        goal(board_client, match, "home", scorer=team_players(lineup, 1)[0].pk, assist="na")
        recorded = Goal.objects.get()
        assert (recorded.assist, recorded.assist_not_applicable) == (None, True)

    def test_tapping_the_scorer_asks_for_the_assist_before_recording(
        self, match: Match, lineup: Lineup, board_client: Client
    ) -> None:
        url = reverse("board:ask_assist", kwargs={"match_id": match.pk, "side": "home"})
        response = board_client.get(url, {"scorer": team_players(lineup, 1)[0].pk})
        assert response.status_code == 200
        assert not Goal.objects.exists()

    def test_a_tap_on_someone_outside_the_match_does_not_ask(
        self, match: Match, lineup: Lineup, board_client: Client
    ) -> None:
        stranger = team_players(lineup, 3)[0]
        url = reverse("board:ask_assist", kwargs={"match_id": match.pk, "side": "home"})
        response = board_client.get(url, {"scorer": stranger.pk})
        assert response.status_code == 302
        assert response.url == reverse("board:match", kwargs={"match_id": match.pk})

    def test_the_question_waits_for_the_start(
        self, match: Match, lineup: Lineup, board_client: Client, clock: FixedClock
    ) -> None:
        GameDay.objects.filter(pk=match.lineup.game_day_id).update(started_at=None)
        url = reverse("board:ask_assist", kwargs={"match_id": match.pk, "side": "home"})
        response = board_client.get(url, {"scorer": team_players(lineup, 1)[0].pk})
        assert response.status_code == 302

    def test_only_the_board_is_asked(self, match: Match, lineup: Lineup) -> None:
        client = Client()
        client.force_login(team_players(lineup, 1)[0])
        url = reverse("board:ask_assist", kwargs={"match_id": match.pk, "side": "home"})
        assert client.get(url, {"scorer": team_players(lineup, 1)[0].pk}).status_code != 200
