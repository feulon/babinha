from datetime import date

import pytest

from community.domain.profile import match_rows, podium_places, presence_percent
from community.domain.stats import DayFact, GoalFact, MatchFact, Side
from community.domain.voting import standings

A, B, C, D, E = 1, 2, 3, 4, 5


def match(home, away, goals=(), finished=True) -> MatchFact:
    return MatchFact(finished, frozenset(home), frozenset(away), tuple(goals), "Azul", "Branco")


def day(*matches: MatchFact) -> DayFact:
    return DayFact(1, date(2026, 10, 18), frozenset({A, B, C, D, E}), matches)


class TestPresencePercent:
    @pytest.mark.parametrize(
        ("attended", "possible", "expected"),
        [(8, 10, 80), (1, 3, 33), (2, 3, 67), (10, 10, 100), (0, 5, 0)],
    )
    def test_share_of_the_sundays_played(self, attended, possible, expected) -> None:
        assert presence_percent(attended, possible) == expected

    def test_no_sundays_yet_is_unknown_not_zero(self) -> None:
        assert presence_percent(0, 0) is None

    def test_never_above_one_hundred(self) -> None:
        assert presence_percent(5, 4) == 100


class TestPodiumPlaces:
    def test_first_second_third(self) -> None:
        ranked = standings([(A, 1, 3), (B, 2, 3), (C, 3, 3)])
        assert podium_places(ranked) == {A: 1, B: 2, C: 3}

    def test_a_tie_for_first_shares_the_ball_and_pushes_the_rest_down(self) -> None:
        ranked = standings([(A, 1, 1), (B, 1, 1), (C, 3, 1)])
        assert podium_places(ranked) == {A: 1, B: 1, C: 3}

    def test_tied_runners_up_share_their_place(self) -> None:
        ranked = standings([(A, 1, 4), (B, 2, 1), (C, 2, 1)])
        assert podium_places(ranked) == {A: 1, B: 2, C: 2}

    def test_nobody_voted(self) -> None:
        assert podium_places(()) == {}


class TestMatchRows:
    def test_score_from_the_players_side(self) -> None:
        fact = day(
            match([A, B], [C, D], [GoalFact(Side.HOME, A), GoalFact(Side.HOME, B, A)]),
            match([A, B], [C, D], [GoalFact(Side.AWAY, C)]),
            match([A, B], [C, D], []),
        )
        win, loss, draw = match_rows(fact, A)
        assert (win.mine, win.theirs, win.result, win.goals, win.assists) == (2, 0, "V", 1, 1)
        assert (loss.mine, loss.theirs, loss.result) == (0, 1, "D")
        assert (draw.mine, draw.theirs, draw.result) == (0, 0, "E")
        assert [row.order for row in (win, loss, draw)] == [1, 2, 3]

    def test_the_away_side_sees_it_reversed(self) -> None:
        fact = day(match([A], [C], [GoalFact(Side.HOME, A), GoalFact(Side.AWAY, C)] * 2))
        (row,) = match_rows(fact, C)
        assert (row.team, row.opponent) == ("Branco", "Azul")

    def test_only_finished_matches_of_the_player(self) -> None:
        fact = day(
            match([A], [C], [GoalFact(Side.HOME, A)], finished=False),
            match([B], [C]),
            match([A], [D]),
        )
        assert [row.order for row in match_rows(fact, A)] == [3]

    def test_a_substitute_is_found_through_their_goal(self) -> None:
        fact = day(match([A], [C], [GoalFact(Side.AWAY, E)]))
        (row,) = match_rows(fact, E)
        assert (row.mine, row.theirs, row.result) == (1, 0, "V")
