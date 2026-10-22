from datetime import datetime

import pytest

from community.domain.voting import (
    BallotError,
    Phase,
    Standing,
    standings,
    validate_ballot,
    window,
    winners,
)
from tests.conftest import SP

LAST_ENDED = datetime(2026, 10, 18, 11, 20, tzinfo=SP)
# The result comes out when the next list opens, Monday 8h by default.
MONDAY_LIST_OPENS = datetime(2026, 10, 19, 8, 0, tzinfo=SP)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=SP)


def win(now: datetime, *, matches: int = 3, ended=LAST_ENDED, game=True):
    return window(
        now=now,
        closes_at=MONDAY_LIST_OPENS,
        has_game=game,
        matches=matches,
        game_over_at=ended,
    )


class TestWindow:
    def test_opens_only_after_the_sunday_is_finished(self) -> None:
        assert win(at(18, 11, 19)).phase == Phase.WAITING
        assert win(at(18, 11, 20)).phase == Phase.OPEN

    def test_stays_open_until_monday_when_the_list_opens(self) -> None:
        assert win(at(19, 7, 59)).phase == Phase.OPEN
        closed = win(at(19, 8, 0))
        assert closed.phase == Phase.CLOSED
        assert closed.closes_at == at(19, 8, 0)

    def test_everyone_voting_does_not_close_it_before_monday(self) -> None:
        assert win(at(18, 20, 0)).phase == Phase.OPEN

    def test_the_clock_alone_does_not_open_the_vote(self) -> None:
        assert win(at(18, 12, 0), ended=None).phase == Phase.WAITING
        assert win(at(18, 18, 0), ended=None).phase == Phase.WAITING

    @pytest.mark.parametrize("kwargs", [{"game": False}, {"matches": 0, "ended": None}])
    def test_no_game_no_vote(self, kwargs: dict) -> None:
        assert win(at(19, 9), **kwargs).phase == Phase.NONE


class TestBallot:
    ELIGIBLE = [1, 2, 3, 4, 5]

    def check(self, **picks: int | None) -> dict[int, int]:
        by_place = {1: picks.get("first"), 2: picks.get("second"), 3: picks.get("third")}
        return validate_ballot(voter=picks.get("voter", 1), picks=by_place, eligible=self.ELIGIBLE)

    def test_three_different_players(self) -> None:
        assert self.check(first=2, second=3, third=4) == {1: 2, 2: 3, 3: 4}

    @pytest.mark.parametrize(
        "picks",
        [
            {"first": 2, "second": 3},
            {"first": 2, "second": 2, "third": 3},
            {"first": 1, "second": 2, "third": 3},
            {"first": 2, "second": 3, "third": 9},
            {"first": 2, "second": 3, "third": 4, "voter": 9},
        ],
        ids=["missing", "repeated", "self", "stranger", "voter-did-not-play"],
    )
    def test_invalid_ballots(self, picks: dict) -> None:
        with pytest.raises(BallotError):
            self.check(**picks)


class TestStandings:
    def test_5_3_2_points(self) -> None:
        rows = standings([(7, 1, 2), (7, 2, 1), (8, 3, 4), (9, 2, 1)])
        assert [(r.player_id, r.points) for r in rows] == [(7, 13), (8, 8), (9, 3)]
        assert rows[0].places == (2, 1, 0)

    def test_ties_go_to_more_first_places_then_second(self) -> None:
        # 7: 5 points from one 1st; 8: 5 points from a 2nd and a 3rd.
        rows = standings([(7, 1, 1), (8, 2, 1), (8, 3, 1)])
        assert [r.player_id for r in rows] == [7, 8]
        assert len(winners(rows)) == 1

    def test_ties_with_equal_first_places_go_to_more_second_places(self) -> None:
        rows = standings([(7, 1, 1), (7, 2, 2), (8, 1, 1), (8, 3, 3)])
        assert [r.points for r in rows] == [11, 11]
        assert [r.player_id for r in winners(rows)] == [7]

    def test_still_tied_after_the_tie_breaks_shares_the_ball(self) -> None:
        rows = standings([(7, 1, 1), (8, 1, 1), (9, 2, 1)])
        assert {r.player_id for r in winners(rows)} == {7, 8}

    def test_nobody_voted_nobody_wins(self) -> None:
        assert standings([]) == ()
        assert winners(()) == ()

    def test_standing_orders_by_points_then_places(self) -> None:
        assert Standing(1, 3, (1, 0, 0)).sort_key > Standing(2, 3, (0, 1, 1)).sort_key
