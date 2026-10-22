from datetime import date

from community.domain.ranking import Metric, rank
from community.domain.seasons import season_index
from community.domain.stats import (
    DayFact,
    GoalFact,
    MatchFact,
    Side,
    Totals,
    day_lines,
    totals,
)

A, B, C, D, E, KEEPER = 1, 2, 3, 4, 5, 6


def match(home, away, goals=(), finished=True) -> MatchFact:
    return MatchFact(finished, frozenset(home), frozenset(away), tuple(goals))


def day(*matches: MatchFact, day_id: int = 1, attendees=(A, B, C, D, E, KEEPER)) -> DayFact:
    return DayFact(day_id, date(2026, 10, 18), frozenset(attendees), matches)


class TestDayLines:
    def test_win_draw_and_loss_by_side(self) -> None:
        fact = day(
            match([A, B], [C, D], [GoalFact(Side.HOME, A, B), GoalFact(Side.HOME, A)]),
            match([A, B], [C, D], [GoalFact(Side.AWAY, C), GoalFact(Side.HOME, B)]),
            match([A, B], [C, D], [GoalFact(Side.AWAY, D, C)]),
        )
        lines = day_lines(fact)
        assert (lines[A].wins, lines[A].draws, lines[A].losses) == (1, 1, 1)
        assert (lines[D].wins, lines[D].draws, lines[D].losses) == (1, 1, 1)
        assert (lines[A].goals, lines[A].assists) == (2, 0)
        assert (lines[B].goals, lines[B].assists) == (1, 1)
        assert (lines[C].goals, lines[C].assists) == (1, 1)
        assert lines[A].matches == 3

    def test_own_goals_count_for_the_score_but_not_for_a_player(self) -> None:
        fact = day(match([A], [C], [GoalFact(Side.HOME), GoalFact(Side.HOME)]))
        lines = day_lines(fact)
        assert lines[A].wins == 1
        assert lines[C].losses == 1
        assert lines[A].goals == 0

    def test_a_substitute_is_found_through_their_goal(self) -> None:
        fact = day(match([A], [C], [GoalFact(Side.AWAY, E)]))
        lines = day_lines(fact)
        assert lines[E].matches == 1
        assert lines[E].wins == 1
        assert lines[A].losses == 1

    def test_goals_count_even_if_the_match_was_not_ended(self) -> None:
        fact = day(match([A], [C], [GoalFact(Side.HOME, A)], finished=False))
        lines = day_lines(fact)
        assert lines[A].goals == 1
        assert lines[A].matches == 0
        assert lines[A].wins == 0

    def test_a_day_nothing_was_played_on_has_no_lines(self) -> None:
        assert day_lines(day(match([A], [C], finished=False))) == {}
        assert day_lines(day()) == {}

    def test_presence_without_playing(self) -> None:
        lines = day_lines(day(match([A], [C])))
        assert lines[KEEPER].present
        assert lines[KEEPER].matches == 0


class TestTotals:
    def test_adds_up_days_and_gold_balls(self) -> None:
        first = day(match([A], [C], [GoalFact(Side.HOME, A)]), day_id=1)
        second = day(match([A], [C], [GoalFact(Side.AWAY, C)]), day_id=2)
        table = totals([first, second], {1: [A], 2: [A, C]})
        assert table[A] == Totals(sundays=2, matches=2, wins=1, losses=1, goals=1, gold_balls=2)
        assert table[C].gold_balls == 1
        assert table[C].sundays == 2


class TestRank:
    NAMES = {1: "Ana", 2: "Bia", 3: "Caio", 4: "Duda"}

    def test_orders_by_the_chosen_metric_and_ties_share_a_position(self) -> None:
        table = {
            1: Totals(sundays=3, goals=5),
            2: Totals(sundays=3, goals=5, assists=2),
            3: Totals(sundays=2, goals=9),
            4: Totals(sundays=1, goals=5),
        }
        rows = rank(table, Metric.GOALS, self.NAMES)
        assert [(r.position, r.player_id) for r in rows] == [(1, 3), (2, 2), (3, 1), (4, 4)]

    def test_players_equal_on_everything_share_the_position(self) -> None:
        table = {1: Totals(sundays=2, goals=1), 2: Totals(sundays=2, goals=1), 3: Totals(sundays=1)}
        rows = rank(table, Metric.GOALS, self.NAMES)
        assert [(r.position, r.player_id) for r in rows] == [(1, 1), (1, 2), (3, 3)]

    def test_gold_balls_lead_the_ties(self) -> None:
        table = {1: Totals(sundays=3, goals=5), 2: Totals(sundays=3, goals=5, gold_balls=1)}
        rows = rank(table, Metric.GOALS, self.NAMES)
        assert [r.player_id for r in rows] == [2, 1]

    def test_players_with_nothing_are_left_out(self) -> None:
        assert rank({1: Totals()}, Metric.GOALS, self.NAMES) == ()


def test_season_index() -> None:
    ends = [date(2026, 6, 28), date(2026, 12, 20)]
    assert season_index(date(2026, 6, 28), ends) == 0
    assert season_index(date(2026, 7, 5), ends) == 1
    assert season_index(date(2026, 12, 20), ends) == 1
    assert season_index(date(2026, 12, 27), ends) == 2
    assert season_index(date(2026, 1, 4), []) == 0
