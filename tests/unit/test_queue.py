import pytest

from matchday.domain.queue import PlayedMatch, next_goalkeepers, next_matchup, waiting_line

FOUR = [1, 2, 3, 4]


def win(home: int, away: int, *, home_goals: int = 2, away_goals: int = 0, **keepers: int):
    return PlayedMatch(home, away, home_goals, away_goals, **keepers)


def draw(home: int, away: int, goals: int = 1):
    return PlayedMatch(home, away, goals, goals)


def play(teams: list[int], results: list[tuple[int, int]]) -> list[PlayedMatch]:
    """Play a Sunday: each result is the (home, away) goals of the match the queue calls."""
    played: list[PlayedMatch] = []
    for home_goals, away_goals in results:
        home, away = next_matchup(teams, played)
        played.append(PlayedMatch(home, away, home_goals, away_goals))
    return played


class TestNextMatchup:
    def test_the_first_match_is_the_first_two_teams(self) -> None:
        assert next_matchup(FOUR, []) == (1, 2)

    def test_the_winner_stays_and_the_longest_waiting_team_enters(self) -> None:
        assert next_matchup(FOUR, [win(1, 2)]) == (1, 3)

    def test_the_winner_stays_even_when_it_was_the_away_side(self) -> None:
        assert next_matchup(FOUR, [win(1, 2, home_goals=0, away_goals=2)]) == (2, 3)

    def test_the_loser_goes_to_the_back_of_the_line(self) -> None:
        # 1 beats 2, then 1 beats 3: 4 has waited since the start, 2 since the first match.
        played = play(FOUR, [(2, 0), (2, 1)])
        assert next_matchup(FOUR, played) == (1, 4)

    def test_a_draw_sends_both_teams_out_and_brings_in_the_two_who_waited(self) -> None:
        assert next_matchup(FOUR, [draw(1, 2)]) == (3, 4)

    def test_after_a_draw_the_pair_that_sat_out_comes_back_in_the_order_they_left(self) -> None:
        played = play(FOUR, [(1, 1), (0, 0)])
        assert next_matchup(FOUR, played) == (1, 2)

    def test_a_match_closed_at_full_time_with_the_lead_is_won_by_the_leader(self) -> None:
        assert next_matchup(FOUR, [win(1, 2, home_goals=1, away_goals=0)]) == (1, 3)

    def test_a_scoreless_match_is_a_draw(self) -> None:
        assert next_matchup(FOUR, [draw(1, 2, goals=0)]) == (3, 4)

    def test_the_team_off_court_longest_goes_first_whatever_its_number(self) -> None:
        # 4 beats 3 at the start; 4 then beats 1 and 2 in turn: 3 has waited the longest.
        played = [win(3, 4, home_goals=0, away_goals=2)]
        played.append(win(4, 1))
        played.append(win(4, 2))
        assert next_matchup([3, 4, 1, 2], played[:1]) == (4, 1)
        assert next_matchup([3, 4, 1, 2], played) == (4, 3)

    def test_three_teams_the_winner_stays_against_the_one_that_waited(self) -> None:
        teams = [1, 2, 3]
        assert next_matchup(teams, [win(1, 2)]) == (1, 3)
        assert next_matchup(teams, [win(1, 2), win(1, 3)]) == (1, 2)

    def test_three_teams_a_draw_brings_in_the_third_and_the_one_who_played_less(self) -> None:
        teams = [1, 2, 3]
        # 1 and 2 draw: 3 comes in with the first of the two that left.
        assert next_matchup(teams, [draw(1, 2)]) == (3, 1)
        # 3 beat 1, then 3 and 2 drew: 1 waited the longest and comes in with the first of them.
        played = [draw(1, 2), win(3, 1), draw(3, 2)]
        assert next_matchup(teams, played) == (1, 2)

    def test_two_teams_always_play_each_other(self) -> None:
        assert next_matchup([5, 6], [win(5, 6)]) == (5, 6)
        assert next_matchup([5, 6], [draw(5, 6)]) == (5, 6)

    def test_more_than_four_teams_use_the_longest_wait(self) -> None:
        teams = [1, 2, 3, 4, 5]
        played = [win(1, 2), win(1, 3), win(1, 4)]
        assert next_matchup(teams, played) == (1, 5)
        played.append(win(1, 5))
        assert next_matchup(teams, played) == (1, 2)

    def test_a_single_team_cannot_play(self) -> None:
        with pytest.raises(ValueError, match="at least two"):
            next_matchup([1], [])

    def test_the_line_after_a_whole_sunday_never_skips_a_team(self) -> None:
        results = [(2, 1), (0, 2), (1, 1), (2, 0), (0, 0), (1, 2), (2, 0)]
        played = play(FOUR, results)
        # A team never waits more than two matches in a row with four teams, draw or not.
        sat_out = dict.fromkeys(FOUR, 0)
        for match in played:
            for team in FOUR:
                sat_out[team] = 0 if team in match.teams else sat_out[team] + 1
                assert sat_out[team] <= 2


class TestWaitingLine:
    def test_before_any_match_the_line_is_the_team_order(self) -> None:
        assert waiting_line(FOUR, []) == [1, 2, 3, 4]

    def test_the_winner_is_on_court_and_not_in_the_line(self) -> None:
        assert waiting_line(FOUR, [win(1, 2)]) == [3, 4, 2]

    def test_after_a_draw_everyone_is_in_the_line(self) -> None:
        assert waiting_line(FOUR, [draw(1, 2)]) == [3, 4, 1, 2]


class TestNextGoalkeepers:
    POOL = [10, 11, 12]

    def test_the_first_match_takes_the_pool_in_order(self) -> None:
        assert next_goalkeepers(self.POOL, [], (1, 2)) == (10, 11)

    def test_the_team_that_stays_keeps_its_goalkeeper(self) -> None:
        played = [win(1, 2, home_goalkeeper=10, away_goalkeeper=11)]
        assert next_goalkeepers(self.POOL, played, (1, 3)) == (10, 12)

    def test_a_winner_that_was_the_away_side_keeps_its_seat_on_the_new_side(self) -> None:
        played = [win(1, 2, home_goals=0, away_goals=2, home_goalkeeper=10, away_goalkeeper=11)]
        assert next_goalkeepers(self.POOL, played, (2, 3)) == (11, 12)

    def test_after_a_draw_the_goalkeepers_who_waited_the_longest_play(self) -> None:
        played = [
            PlayedMatch(1, 2, 1, 1, 10, 11),
            PlayedMatch(3, 4, 1, 1, 12, 10),
            PlayedMatch(1, 3, 0, 0, 11, 12),
        ]
        assert next_goalkeepers(self.POOL, played, (2, 4)) == (10, 11)

    def test_a_single_goalkeeper_leaves_the_other_seat_open(self) -> None:
        assert next_goalkeepers([10], [], (1, 2)) == (10, None)

    def test_no_goalkeeper_leaves_both_seats_open(self) -> None:
        assert next_goalkeepers([], [], (1, 2)) == (None, None)

    def test_a_staying_goalkeeper_who_left_the_pool_is_replaced(self) -> None:
        played = [win(1, 2, home_goalkeeper=99, away_goalkeeper=11)]
        assert next_goalkeepers(self.POOL, played, (1, 3)) == (10, 12)
