import random
from collections import Counter
from collections.abc import Callable
from datetime import datetime

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction

from accounts.models import Player
from core.clock import FixedClock
from games import services as games
from games.models import Attendance
from matchday import services
from matchday.domain.score import Score, Side
from matchday.models import Goal, Lineup, LineupSpot, Match
from matchday.services import MatchdayError
from tests.conftest import SP, TUESDAY, Sunday

pytestmark = pytest.mark.django_db

AFTER_KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)


def team_of(lineup: Lineup, player: Player) -> int | None:
    spot = LineupSpot.objects.select_related("team").get(lineup=lineup, player=player)
    return spot.team.number if spot.team else None


def members(lineup: Lineup, number: int) -> list[Player]:
    return [
        spot.player
        for spot in LineupSpot.objects.filter(lineup=lineup, team__number=number)
        .select_related("player")
        .order_by("pk")
    ]


def drawn(fill_sunday: Callable[..., Sunday], board: Player, **kw) -> tuple[Sunday, Lineup]:
    sunday = fill_sunday(**kw)
    return sunday, services.draw(sunday.game_day.pk, board, random.Random(1))


class TestDraw:
    def test_four_teams_of_five_balanced_by_pote(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        _sunday, lineup = drawn(fill_sunday, board_member)
        assert [team.name for team in lineup.teams.all()] == [
            "Time A",
            "Time B",
            "Time C",
            "Time D",
        ]
        for number in range(1, 5):
            team = members(lineup, number)
            assert len(team) == 5
            assert Counter(p.pote for p in team) == Counter({1: 1, 2: 1, 3: 1, 4: 1, 5: 1})
        assert not lineup.is_confirmed
        assert services.in_sync(lineup)

    @pytest.mark.parametrize(("goalkeepers", "line", "in_goal"), [(1, 21, 1), (0, 22, 2)])
    def test_extra_line_players_go_to_goal(
        self,
        fill_sunday: Callable[..., Sunday],
        board_member: Player,
        goalkeepers: int,
        line: int,
        in_goal: int,
    ) -> None:
        _, lineup = drawn(fill_sunday, board_member, line=line, goalkeepers=goalkeepers)
        assert lineup.spots.filter(team=None).count() == in_goal
        pool = services.goalkeeper_pool(lineup)
        assert len(pool) == goalkeepers + in_goal

    def test_absent_players_are_left_out(
        self, fill_sunday: Callable[..., Sunday], board_member: Player, clock: FixedClock
    ) -> None:
        sunday = fill_sunday(line=21, goalkeepers=1)
        clock.set(AFTER_KICKOFF)
        games.mark_attendance(sunday.game_day.pk, sunday.line[0], Attendance.FALTOU, board_member)
        lineup = services.draw(sunday.game_day.pk, board_member, random.Random(2))
        assert not lineup.spots.filter(player=sunday.line[0]).exists()
        assert lineup.spots.count() == 20
        assert lineup.spots.filter(team=None).count() == 0

    def test_redraw_keeps_names_and_matches(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday, lineup = drawn(fill_sunday, board_member)
        services.rename_teams(
            sunday.game_day.pk, board_member, {1: "Azul", 2: "Verde", 3: "Branco", 4: "Preto"}
        )
        services.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)
        again = services.draw(sunday.game_day.pk, board_member, random.Random(99))
        assert again.pk == lineup.pk
        assert [team.name for team in again.teams.all()] == ["Azul", "Verde", "Branco", "Preto"]
        assert again.matches.count() == 1
        assert again.spots.count() == 20

    def test_confirmed_teams_cannot_be_redrawn(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday, _ = drawn(fill_sunday, board_member)
        services.confirm(sunday.game_day.pk, board_member)
        with pytest.raises(MatchdayError, match="Desfaça a confirmação"):
            services.draw(sunday.game_day.pk, board_member)
        services.unconfirm(sunday.game_day.pk, board_member)
        services.draw(sunday.game_day.pk, board_member)

    def test_needs_four_line_players(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday = fill_sunday(line=3)
        with pytest.raises(MatchdayError, match="pelo menos 4"):
            services.draw(sunday.game_day.pk, board_member)

    def test_only_the_board(self, fill_sunday: Callable[..., Sunday]) -> None:
        sunday = fill_sunday()
        with pytest.raises(PermissionDenied):
            services.draw(sunday.game_day.pk, sunday.line[0])

    def test_not_on_a_cancelled_sunday(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday = fill_sunday()
        games.cancel_game_day(sunday.game_day.pk, board_member, "Chuva")
        with pytest.raises(MatchdayError, match="cancelado"):
            services.draw(sunday.game_day.pk, board_member)


class TestAdjustAndConfirm:
    def test_move_a_player_between_teams_and_into_goal(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        # 21 line players: one keeps goal. Swap who it is.
        sunday, lineup = drawn(fill_sunday, board_member, line=21, goalkeepers=1)
        assignments = {
            spot.player_id: spot.team.number if spot.team else None for spot in lineup.spots.all()
        }
        keeper = next(p for p in sunday.line if assignments[p.pk] is None)
        mover = next(p for p in sunday.line if assignments[p.pk] is not None)
        target = assignments[mover.pk]
        services.assign(
            sunday.game_day.pk, board_member, assignments | {mover.pk: None, keeper.pk: target}
        )
        assert team_of(lineup, mover) is None
        assert team_of(lineup, keeper) == target
        assert mover in services.goalkeeper_pool(lineup)

    def test_nobody_goes_to_goal_with_twenty_line_players(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday, lineup = drawn(fill_sunday, board_member)
        assignments = {spot.player_id: spot.team.number for spot in lineup.spots.all()}
        with pytest.raises(MatchdayError, match="ninguém precisa ir ao gol"):
            services.assign(
                sunday.game_day.pk, board_member, assignments | {sunday.line[0].pk: None}
            )
        assert team_of(lineup, sunday.line[0]) is not None

    def test_only_the_line_players_beyond_twenty_go_to_goal(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday, lineup = drawn(fill_sunday, board_member, line=22, goalkeepers=0)
        assignments = {
            spot.player_id: spot.team.number if spot.team else None for spot in lineup.spots.all()
        }
        in_team = next(p for p in sunday.line if assignments[p.pk] is not None)
        with pytest.raises(MatchdayError, match="só 2 vão ao gol"):
            services.assign(sunday.game_day.pk, board_member, assignments | {in_team.pk: None})

    def test_a_goal_that_is_too_full_can_still_be_emptied(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday, lineup = drawn(fill_sunday, board_member, line=22, goalkeepers=0)
        assignments = {
            spot.player_id: spot.team.number if spot.team else None for spot in lineup.spots.all()
        }
        games.leave_list(sunday.game_day.pk, sunday.line[0], board_actor=board_member)
        assignments.pop(sunday.line[0].pk)
        # 21 line players now, two of them in goal: leaving one there is allowed, adding is not.
        in_goal = [pid for pid, number in assignments.items() if number is None]
        services.assign(sunday.game_day.pk, board_member, assignments)
        assert len(in_goal) in (1, 2)
        services.assign(sunday.game_day.pk, board_member, assignments | {in_goal[0]: 1})
        assert LineupSpot.objects.get(lineup=lineup, player_id=in_goal[0]).team.number == 1

    def test_assignments_must_match_the_list(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday, lineup = drawn(fill_sunday, board_member)
        assignments = {spot.player_id: spot.team.number for spot in lineup.spots.all()}
        games.leave_list(sunday.game_day.pk, sunday.line[0], board_actor=board_member)
        with pytest.raises(MatchdayError, match="A lista mudou"):
            services.assign(sunday.game_day.pk, board_member, assignments)

    def test_unknown_team_is_rejected(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday, lineup = drawn(fill_sunday, board_member)
        assignments = {spot.player_id: 5 for spot in lineup.spots.all()}
        with pytest.raises(MatchdayError, match="Time inválido"):
            services.assign(sunday.game_day.pk, board_member, assignments)

    def test_confirm_needs_the_lineup_to_match_the_list(
        self, fill_sunday: Callable[..., Sunday], board_member: Player, make_player
    ) -> None:
        sunday, lineup = drawn(fill_sunday, board_member)
        leaving = sunday.line[0]
        games.leave_list(sunday.game_day.pk, leaving, board_actor=board_member)
        newcomer = make_player("Novato")
        games.join_list(sunday.game_day.pk, newcomer)
        assert not services.in_sync(lineup)
        with pytest.raises(MatchdayError, match="A lista mudou depois do sorteio"):
            services.confirm(sunday.game_day.pk, board_member)

        assignments = {
            spot.player_id: spot.team.number
            for spot in lineup.spots.exclude(player=leaving).select_related("team")
        }
        services.assign(
            sunday.game_day.pk,
            board_member,
            assignments | {newcomer.pk: team_of(lineup, leaving)},
        )
        confirmed = services.confirm(sunday.game_day.pk, board_member)
        assert confirmed.is_confirmed
        assert confirmed.confirmed_by == board_member

    def test_every_team_needs_a_player(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday, lineup = drawn(fill_sunday, board_member)
        everyone_on_one = {spot.player_id: 1 for spot in lineup.spots.all()}
        services.assign(sunday.game_day.pk, board_member, everyone_on_one)
        with pytest.raises(MatchdayError, match="pelo menos um jogador"):
            services.confirm(sunday.game_day.pk, board_member)

    def test_confirmed_adjustment_cannot_empty_a_team(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday, lineup = drawn(fill_sunday, board_member)
        services.confirm(sunday.game_day.pk, board_member)
        before = {spot.player_id: spot.team.number for spot in lineup.spots.all()}
        no_team_d = {pid: 1 if number == 4 else number for pid, number in before.items()}
        with pytest.raises(MatchdayError, match="pelo menos um jogador"):
            services.assign(sunday.game_day.pk, board_member, no_team_d)
        assert {spot.player_id: spot.team.number for spot in lineup.spots.all()} == before

    def test_confirm_and_unconfirm_only_once(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday, _ = drawn(fill_sunday, board_member)
        with pytest.raises(MatchdayError, match="ainda não foram confirmados"):
            services.unconfirm(sunday.game_day.pk, board_member)
        services.confirm(sunday.game_day.pk, board_member)
        with pytest.raises(MatchdayError, match="já estão confirmados"):
            services.confirm(sunday.game_day.pk, board_member)

    def test_nothing_before_the_draw(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday = fill_sunday()
        with pytest.raises(MatchdayError, match="Sorteie os times"):
            services.confirm(sunday.game_day.pk, board_member)


def recorded(match: Match) -> list[tuple[int, int | None, int | None]]:
    return list(match.goals.order_by("pk").values_list("team_id", "scorer_id", "assist_id"))


class TestEditableAfterResults:
    def test_redraw_after_goals_keeps_the_recorded_goals(
        self, fill_sunday: Callable[..., Sunday], board_member: Player, clock: FixedClock
    ) -> None:
        sunday, lineup = drawn(fill_sunday, board_member)
        services.confirm(sunday.game_day.pk, board_member)
        match = services.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)
        clock.set(AFTER_KICKOFF)
        home, away = members(lineup, 1), members(lineup, 2)
        services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=home[0].pk, assist=home[1].pk
        )
        services.add_goal(match.pk, board_member, side=Side.AWAY, scorer=away[0].pk)
        services.finish_match(match.pk, board_member)
        goals = recorded(match)

        services.unconfirm(sunday.game_day.pk, board_member)
        services.draw(sunday.game_day.pk, board_member, random.Random(99))
        services.confirm(sunday.game_day.pk, board_member)

        match.refresh_from_db()
        assert recorded(match) == goals
        assert services.score_of(match) == Score(1, 1)
        assert match.is_finished

    def test_adjust_confirmed_teams_after_goals(
        self,
        fill_sunday: Callable[..., Sunday],
        board_member: Player,
        make_player,
        clock: FixedClock,
    ) -> None:
        sunday, lineup = drawn(fill_sunday, board_member)
        services.confirm(sunday.game_day.pk, board_member)
        match = services.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)
        clock.set(AFTER_KICKOFF)
        scorer = members(lineup, 1)[0]
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
        goals = recorded(match)

        clock.set(TUESDAY)  # the list is still open for changes, as when the teams were drawn
        games.leave_list(sunday.game_day.pk, sunday.line[5], board_actor=board_member)
        newcomer = make_player("Novato")
        games.join_list(sunday.game_day.pk, newcomer)
        assignments = {
            spot.player_id: spot.team.number
            for spot in lineup.spots.exclude(player=sunday.line[5]).select_related("team")
        }
        services.assign(
            sunday.game_day.pk, board_member, assignments | {newcomer.pk: 1, scorer.pk: 3}
        )

        assert team_of(lineup, newcomer) == 1
        assert team_of(lineup, scorer) == 3
        assert recorded(match) == goals
        assert services.score_of(match) == Score(1, 0)


class TestRename:
    def test_swap_names(self, fill_sunday: Callable[..., Sunday], board_member: Player) -> None:
        sunday, lineup = drawn(fill_sunday, board_member)
        services.rename_teams(
            sunday.game_day.pk, board_member, {1: "Time B", 2: "Time A", 3: " Time  C ", 4: "D"}
        )
        assert [t.name for t in lineup.teams.all()] == ["Time B", "Time A", "Time C", "D"]

    @pytest.mark.parametrize(
        ("names", "error"),
        [
            ({1: "Azul", 2: "azul", 3: "C", 4: "D"}, "nomes diferentes"),
            ({1: "  ", 2: "B", 3: "C", 4: "D"}, "precisa de um nome"),
            ({1: "A", 2: "B", 3: "C"}, "todos os times"),
            ({1: "x" * 31, 2: "B", 3: "C", 4: "D"}, "no máximo 30"),
        ],
    )
    def test_invalid_names(
        self,
        fill_sunday: Callable[..., Sunday],
        board_member: Player,
        names: dict[int, str],
        error: str,
    ) -> None:
        sunday, _ = drawn(fill_sunday, board_member)
        with pytest.raises(MatchdayError, match=error):
            services.rename_teams(sunday.game_day.pk, board_member, names)


@pytest.fixture
def match_setup(
    fill_sunday: Callable[..., Sunday], board_member: Player, clock: FixedClock
) -> tuple[Sunday, Lineup, Match]:
    sunday, lineup = drawn(fill_sunday, board_member)
    clock.set(AFTER_KICKOFF)
    match = services.add_match(
        sunday.game_day.pk,
        board_member,
        home_team=1,
        away_team=2,
        home_goalkeeper=sunday.goalkeepers[0].pk,
        away_goalkeeper=sunday.goalkeepers[1].pk,
    )
    return sunday, lineup, match


class TestMatchTable:
    def test_matches_are_numbered_in_order(self, match_setup: tuple, board_member: Player) -> None:
        sunday, _lineup, first = match_setup
        second = services.add_match(sunday.game_day.pk, board_member, home_team=3, away_team=4)
        assert (first.order, second.order) == (1, 2)
        assert second.home_goalkeeper is None

    def test_the_same_match_twice_in_a_row_is_refused(
        self, match_setup: tuple, board_member: Player
    ) -> None:
        sunday, _lineup, first = match_setup
        keepers = (sunday.goalkeepers[0].pk, sunday.goalkeepers[1].pk)
        with pytest.raises(MatchdayError, match="já é a última"):
            services.add_match(
                sunday.game_day.pk,
                board_member,
                home_team=1,
                away_team=2,
                home_goalkeeper=keepers[0],
                away_goalkeeper=keepers[1],
            )
        assert first.lineup.matches.count() == 1

    def test_the_same_match_is_fine_after_another_one(
        self, match_setup: tuple, board_member: Player
    ) -> None:
        sunday, _lineup, _first = match_setup
        services.add_match(sunday.game_day.pk, board_member, home_team=3, away_team=4)
        again = services.add_match(
            sunday.game_day.pk,
            board_member,
            home_team=1,
            away_team=2,
            home_goalkeeper=sunday.goalkeepers[0].pk,
            away_goalkeeper=sunday.goalkeepers[1].pk,
        )
        assert again.order == 3

    @pytest.mark.parametrize(
        ("home", "away", "error"),
        [(1, 1, "dois times diferentes"), (1, 9, "Time inválido")],
    )
    def test_invalid_teams(
        self, match_setup: tuple, board_member: Player, home: int, away: int, error: str
    ) -> None:
        sunday, _, _ = match_setup
        with pytest.raises(MatchdayError, match=error):
            services.add_match(sunday.game_day.pk, board_member, home_team=home, away_team=away)

    def test_goalkeepers_come_from_the_pool_and_differ(
        self, match_setup: tuple, board_member: Player
    ) -> None:
        sunday, _, match = match_setup
        keeper = sunday.goalkeepers[0]
        with pytest.raises(MatchdayError, match="goleiro diferente"):
            services.set_goalkeepers(match.pk, board_member, home=keeper.pk, away=keeper.pk)
        with pytest.raises(MatchdayError, match="Escolha goleiros"):
            services.set_goalkeepers(match.pk, board_member, home=sunday.line[0].pk, away=None)
        services.set_goalkeepers(match.pk, board_member, home=None, away=keeper.pk)
        match.refresh_from_db()
        assert (match.home_goalkeeper, match.away_goalkeeper) == (None, keeper)

    def test_recorded_goalkeeper_stays_valid_after_leaving_the_pool(
        self, match_setup: tuple, board_member: Player, clock: FixedClock
    ) -> None:
        sunday, lineup, match = match_setup
        gone, other = sunday.goalkeepers[0], sunday.goalkeepers[1]
        clock.set(AFTER_KICKOFF)
        games.mark_attendance(sunday.game_day.pk, gone, Attendance.FALTOU, board_member)
        assert gone not in services.goalkeeper_pool(lineup)
        assert gone in services.match_goalkeeper_pool(match)
        services.set_goalkeepers(match.pk, board_member, home=gone.pk, away=None)
        match.refresh_from_db()
        assert (match.home_goalkeeper, match.away_goalkeeper) == (gone, None)
        services.set_goalkeepers(match.pk, board_member, home=None, away=other.pk)
        with pytest.raises(MatchdayError, match="Escolha goleiros"):
            services.set_goalkeepers(match.pk, board_member, home=gone.pk, away=other.pk)
        with pytest.raises(MatchdayError, match="Escolha goleiros"):
            services.add_match(
                sunday.game_day.pk, board_member, home_team=3, away_team=4, home_goalkeeper=gone.pk
            )

    def test_match_needs_teams(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday = fill_sunday()
        with pytest.raises(MatchdayError, match="Sorteie os times"):
            services.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)

    def test_delete_a_match_with_its_goals(self, match_setup: tuple, board_member: Player) -> None:
        _, lineup, match = match_setup
        scorer = members(lineup, 1)[0]
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
        services.delete_match(match.pk, board_member)
        assert not Match.objects.exists()
        assert not Goal.objects.exists()


class TestGoals:
    def test_two_goals_end_the_match(self, match_setup: tuple, board_member: Player) -> None:
        _sunday, lineup, match = match_setup
        home, away = members(lineup, 1), members(lineup, 2)
        services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=home[0].pk, assist=home[1].pk
        )
        services.add_goal(match.pk, board_member, side=Side.AWAY, scorer=away[0].pk)
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[2].pk)
        match.refresh_from_db()
        assert match.is_finished
        assert match.finished_by == board_member
        assert services.score_of(match).home == 2
        with pytest.raises(MatchdayError, match="já acabou"):
            services.add_goal(match.pk, board_member, side=Side.AWAY, scorer=away[1].pk)

    def test_goalkeeper_can_score_and_assist_for_their_side(
        self, match_setup: tuple, board_member: Player
    ) -> None:
        sunday, lineup, match = match_setup
        keeper = sunday.goalkeepers[0]
        striker = members(lineup, 1)[0]
        goal = services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=striker.pk, assist=keeper.pk
        )
        assert goal.assist == keeper
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=keeper.pk)
        assert Goal.objects.filter(scorer=keeper).count() == 1

    def test_anyone_on_the_list_can_score_or_assist(
        self, match_setup: tuple, board_member: Player
    ) -> None:
        _, lineup, match = match_setup
        home, substitute = members(lineup, 1), members(lineup, 3)[0]
        goal = services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=substitute.pk, assist=home[0].pk
        )
        assert (goal.team, goal.scorer, goal.assist) == (match.home_team, substitute, home[0])

    def test_scorer_and_assist_must_be_playing_on_the_list(
        self,
        match_setup: tuple,
        board_member: Player,
        make_player: Callable[..., Player],
        clock: FixedClock,
    ) -> None:
        sunday, lineup, match = match_setup
        home = members(lineup, 1)
        outsider = make_player("De fora")
        with pytest.raises(MatchdayError, match="autor do gol"):
            services.add_goal(match.pk, board_member, side=Side.HOME, scorer=outsider.pk)
        with pytest.raises(MatchdayError, match="assistência"):
            services.add_goal(
                match.pk, board_member, side=Side.HOME, scorer=home[0].pk, assist=outsider.pk
            )
        clock.set(AFTER_KICKOFF)
        games.mark_attendance(sunday.game_day.pk, home[1], Attendance.FALTOU, board_member)
        with pytest.raises(MatchdayError, match="autor do gol"):
            services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[1].pk)
        with pytest.raises(MatchdayError, match="não pode ser também"):
            services.add_goal(
                match.pk, board_member, side=Side.HOME, scorer=home[0].pk, assist=home[0].pk
            )
        with pytest.raises(MatchdayError, match="autor do gol"):
            services.add_goal(match.pk, board_member, side=Side.HOME, scorer=None)
        assert not match.goals.exists()

    def test_the_side_comes_first_in_the_choices(
        self, match_setup: tuple, board_member: Player
    ) -> None:
        sunday, lineup, match = match_setup
        choices = services.side_players(match, Side.HOME)
        side = {*members(lineup, 1), sunday.goalkeepers[0]}
        assert set(choices[: len(side)]) == side
        assert set(choices) == {*sunday.line, *sunday.goalkeepers}
        assert len(choices) == len(sunday.line) + len(sunday.goalkeepers)

    def test_the_assist_answer_is_a_player_nobody_or_not_applicable(
        self, match_setup: tuple, board_member: Player
    ) -> None:
        _, lineup, match = match_setup
        home = members(lineup, 1)
        by_player = services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=home[0].pk, assist=home[1].pk
        )
        nobody = services.add_goal(
            match.pk, board_member, side=Side.AWAY, scorer=members(lineup, 2)[0].pk
        )
        match.refresh_from_db()
        assert (by_player.assist, by_player.assist_not_applicable) == (home[1], False)
        assert (nobody.assist, nobody.assist_not_applicable) == (None, False)

    def test_an_assist_that_does_not_apply_is_kept_apart_from_no_assist(
        self, match_setup: tuple, board_member: Player
    ) -> None:
        _, lineup, match = match_setup
        goal = services.add_goal(
            match.pk,
            board_member,
            side=Side.HOME,
            scorer=members(lineup, 1)[0].pk,
            assist_not_applicable=True,
        )
        goal.refresh_from_db()
        assert goal.assist is None
        assert goal.assist_not_applicable

    def test_not_applicable_cannot_come_with_a_player_or_an_own_goal(
        self, match_setup: tuple, board_member: Player
    ) -> None:
        _, lineup, match = match_setup
        home = members(lineup, 1)
        with pytest.raises(MatchdayError, match="não se aplica"):
            services.add_goal(
                match.pk,
                board_member,
                side=Side.HOME,
                scorer=home[0].pk,
                assist=home[1].pk,
                assist_not_applicable=True,
            )
        with pytest.raises(MatchdayError, match="Gol contra"):
            services.add_goal(
                match.pk,
                board_member,
                side=Side.HOME,
                scorer=None,
                own_goal=True,
                assist_not_applicable=True,
            )
        assert not match.goals.exists()

    def test_correcting_a_goal_changes_the_assist_answer(
        self, match_setup: tuple, board_member: Player
    ) -> None:
        _, lineup, match = match_setup
        home = members(lineup, 1)
        goal = services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=home[0].pk, assist=home[1].pk
        )
        services.edit_goal(
            goal.pk,
            board_member,
            side=Side.HOME,
            scorer=home[0].pk,
            assist_not_applicable=True,
        )
        goal.refresh_from_db()
        assert (goal.assist, goal.assist_not_applicable) == (None, True)
        services.edit_goal(
            goal.pk, board_member, side=Side.HOME, scorer=home[0].pk, assist=home[2].pk
        )
        goal.refresh_from_db()
        assert (goal.assist, goal.assist_not_applicable) == (home[2], False)

    def test_the_database_refuses_a_player_assist_that_does_not_apply(
        self, match_setup: tuple, board_member: Player
    ) -> None:
        _, lineup, match = match_setup
        home = members(lineup, 1)
        with pytest.raises(IntegrityError), transaction.atomic():
            Goal.objects.create(
                match=match,
                team=match.home_team,
                scorer=home[0],
                assist=home[1],
                assist_not_applicable=True,
                created_by=board_member,
            )

    def test_own_goal_has_no_scorer(self, match_setup: tuple, board_member: Player) -> None:
        _, lineup, match = match_setup
        goal = services.add_goal(match.pk, board_member, side=Side.AWAY, scorer=None, own_goal=True)
        assert goal.team == match.away_team
        assert goal.scorer is None
        with pytest.raises(MatchdayError, match="Gol contra"):
            services.add_goal(
                match.pk,
                board_member,
                side=Side.AWAY,
                scorer=members(lineup, 2)[0].pk,
                own_goal=True,
            )

    def test_time_runs_out_on_a_draw(self, match_setup: tuple, board_member: Player) -> None:
        _, lineup, match = match_setup
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=members(lineup, 1)[0].pk)
        services.add_goal(match.pk, board_member, side=Side.AWAY, scorer=members(lineup, 2)[0].pk)
        services.finish_match(match.pk, board_member)
        match.refresh_from_db()
        assert match.is_finished
        assert services.score_of(match).result == "draw"
        with pytest.raises(MatchdayError, match="já foi encerrada"):
            services.finish_match(match.pk, board_member)

    def test_goals_can_be_fixed_without_reopening(
        self, match_setup: tuple, board_member: Player
    ) -> None:
        _, lineup, match = match_setup
        goal = services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=members(lineup, 1)[0].pk
        )
        services.finish_match(match.pk, board_member)
        services.delete_goal(goal.pk, board_member)
        assert not match.goals.exists()
        match.refresh_from_db()
        assert match.is_finished
        services.reopen_match(match.pk, board_member)
        with pytest.raises(MatchdayError, match="ainda não foi encerrada"):
            services.reopen_match(match.pk, board_member)

    def test_only_the_board_records_goals(self, match_setup: tuple) -> None:
        _sunday, lineup, match = match_setup
        scorer = members(lineup, 1)[0]
        with pytest.raises(PermissionDenied):
            services.add_goal(match.pk, scorer, side=Side.HOME, scorer=scorer.pk)
