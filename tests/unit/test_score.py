import pytest

from matchday.domain.score import MatchOver, Result, Score, Side


def test_new_match_is_a_draw_and_open() -> None:
    score = Score()
    assert score.result == Result.DRAW
    assert not score.decided


def test_two_goals_end_the_match() -> None:
    score = Score().add(Side.HOME).add(Side.AWAY).add(Side.HOME)
    assert score == Score(2, 1)
    assert score.decided
    assert score.result == Result.HOME_WIN


def test_no_goal_after_the_match_is_decided() -> None:
    with pytest.raises(MatchOver):
        Score(0, 2).add(Side.HOME)


@pytest.mark.parametrize(
    ("score", "result"),
    [(Score(1, 0), Result.HOME_WIN), (Score(0, 1), Result.AWAY_WIN), (Score(1, 1), Result.DRAW)],
)
def test_result_at_full_time(score: Score, result: Result) -> None:
    assert not score.decided
    assert score.result == result


@pytest.mark.parametrize(("home", "away"), [(-1, 0), (3, 0), (0, 3), (2, 2)])
def test_impossible_scores(home: int, away: int) -> None:
    with pytest.raises(ValueError, match="goal"):
        Score(home, away)
