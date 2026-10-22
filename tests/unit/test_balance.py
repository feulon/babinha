import pytest

from matchday.domain.balance import Imbalance, TeamLoad, assess
from matchday.domain.sizes import goal_full_message, goal_is_over_full, goal_spots


def loads(*teams: tuple[int, ...]) -> list[TeamLoad]:
    return [TeamLoad(team) for team in teams]


EVEN = (1, 2, 3, 4, 5)


class TestAssess:
    def test_even_teams_have_nothing_to_warn_about(self) -> None:
        assert assess(loads(EVEN, EVEN, EVEN, EVEN)) == ()

    def test_no_teams_no_warning(self) -> None:
        assert assess([]) == ()

    def test_a_team_with_more_players_is_a_warning(self) -> None:
        found = assess(loads((*EVEN, 3), EVEN, EVEN, (1, 2, 3, 4)))
        assert found[0] == Imbalance("count", numbers=(6, 5, 5, 4), expected=(5, 5, 5, 5))

    def test_one_team_short_is_expected_when_players_do_not_divide(self) -> None:
        assert assess(loads(EVEN, EVEN, EVEN, (1, 2, 3, 4))) == ()

    def test_the_expected_sizes_follow_who_is_in_teams(self) -> None:
        # 19 players in teams: 5 5 5 4 is right, 6 5 4 4 is not.
        found = assess(loads((*EVEN, 3), EVEN, (1, 2, 3, 4), (1, 2, 3, 4)))
        assert found[0].kind == "count"
        assert found[0].expected == (5, 5, 5, 4)

    def test_teams_of_very_different_strength_are_a_warning(self) -> None:
        found = assess(loads((1, 1, 2, 2, 2), (5, 5, 4, 4, 4), EVEN, EVEN))
        assert Imbalance("strength", averages=(1.6, 4.4), low_team=0, high_team=1) in found

    def test_the_average_is_what_counts_not_the_sum(self) -> None:
        # Four players against six: the sums are far apart, the strength is the same.
        assert assess(loads((3, 3, 3, 3), (3, 3, 3, 3, 3, 3), EVEN, EVEN))[0].kind == "count"
        assert all(i.kind != "strength" for i in assess(loads((3,) * 4, (3,) * 6, EVEN, EVEN)))

    def test_averages_within_the_spread_are_fine(self) -> None:
        assert assess(loads((1, 3, 3, 3, 5), (2, 2, 3, 4, 5), EVEN, EVEN)) == ()

    @pytest.mark.parametrize("counts", [(3, 1, 1, 0), (2, 1, 1, 0), (4, 0, 0, 0)])
    def test_a_pote_is_spread_unevenly_only_from_three_apart(self, counts: tuple[int, ...]) -> None:
        teams = loads(*(tuple([1] * n + [3] * (5 - n)) for n in counts))
        skews = [i for i in assess(teams) if i.kind == "pote"]
        if max(counts) - min(counts) >= 3:
            assert skews[0].pote == 1
            assert (skews[0].high, skews[0].low) == (max(counts), min(counts))
        else:
            assert skews == []


class TestGoal:
    def test_nobody_is_needed_in_goal_with_twenty_line_players(self) -> None:
        # Twenty line players placed: 4 4 5 5 in teams and two of them in goal is wrong.
        found = assess(loads((1, 2, 3, 4), (1, 2, 3, 4), EVEN, EVEN), in_goal=2)
        assert found == (Imbalance("goal", numbers=(2, 20), expected=(0,)),)

    def test_the_right_number_in_goal_is_fine(self) -> None:
        assert assess(loads(EVEN, EVEN, EVEN, EVEN), in_goal=2) == ()
        assert assess(loads(EVEN, EVEN, EVEN, EVEN)) == ()

    def test_beyond_twenty_the_goal_needs_them(self) -> None:
        # 22 line players, two of them should keep goal; with 6 6 5 5 nobody does.
        found = assess(loads((*EVEN, 1), (*EVEN, 2), EVEN, EVEN))
        assert found == (Imbalance("goal", numbers=(0, 22), expected=(2,)),)

    def test_goal_and_sizes_are_judged_apart(self) -> None:
        found = assess(loads((*EVEN, 3), EVEN, EVEN, (1, 2, 3, 4)), in_goal=1)
        assert {i.kind for i in found} == {"count"}

    @pytest.mark.parametrize(("line", "spots"), [(18, 0), (20, 0), (21, 1), (22, 2)])
    def test_goal_spots(self, line: int, spots: int) -> None:
        assert goal_spots(line) == spots

    def test_only_adding_to_a_full_goal_is_refused(self) -> None:
        assert goal_is_over_full(before=0, after=1, placed=20)
        assert goal_is_over_full(before=1, after=2, placed=21)
        assert not goal_is_over_full(before=1, after=2, placed=22)
        assert not goal_is_over_full(before=2, after=2, placed=21)  # already too full: not worse
        assert not goal_is_over_full(before=2, after=1, placed=20)

    def test_the_message_says_why(self) -> None:
        assert "ninguém precisa ir ao gol" in goal_full_message(20)
        assert goal_full_message(21) == "Com 21 de linha só 1 vai ao gol, e ele já está lá."
        assert goal_full_message(22) == "Com 22 de linha só 2 vão ao gol, e eles já estão lá."
