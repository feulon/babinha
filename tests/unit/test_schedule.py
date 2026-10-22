from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from games.domain.schedule import WeekSchedule, follow_sunday, matchday_sunday, upcoming_sunday
from games.domain.slots import fits, line_capacity
from games.domain.types import PlayerType

SP = ZoneInfo("America/Sao_Paulo")
SUNDAY = date(2026, 10, 18)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=SP)


@pytest.fixture
def week() -> WeekSchedule:
    return WeekSchedule(SUNDAY, list_opens_at=time(8, 0))


def test_rejects_a_day_that_is_not_sunday() -> None:
    with pytest.raises(ValueError, match="not a Sunday"):
        WeekSchedule(date(2026, 10, 17), list_opens_at=time(8, 0))


def test_key_moments_of_the_week(week: WeekSchedule) -> None:
    assert week.opens_main == at(12, 8)
    assert week.opens_agregado == at(14, 20)
    assert week.free_until == at(18, 0)
    assert week.starts == at(18, 10)
    assert week.ends == at(18, 12)
    assert week.last_free_minute == at(17, 23, 59)
    assert week.vote_closes == at(19, 8)


def test_monday_opening_hour_is_configurable() -> None:
    week = WeekSchedule(SUNDAY, list_opens_at=time(7, 30))
    assert week.opens_main == at(12, 7, 30)
    assert week.vote_closes == at(19, 7, 30)


def test_opening_depends_on_player_type(week: WeekSchedule) -> None:
    assert week.opens_for(PlayerType.MENSALISTA) == week.opens_main
    assert week.opens_for(PlayerType.AVULSO) == week.opens_main
    assert week.opens_for(PlayerType.AGREGADO) == week.opens_agregado


def test_times_are_in_sao_paulo(week: WeekSchedule) -> None:
    assert week.starts.utcoffset() == datetime(2026, 1, 1, tzinfo=SP).utcoffset()
    assert week.starts.astimezone(ZoneInfo("UTC")).hour == 13


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(12, 0, 1), SUNDAY),  # Monday
        (at(17, 23, 59), SUNDAY),  # Saturday night
        (at(18, 11, 59), SUNDAY),  # during the game
        (at(18, 12, 0), date(2026, 10, 25)),  # game over: next week
        (at(11, 13), SUNDAY),  # previous Sunday after the game
        (at(11, 9), date(2026, 10, 11)),  # previous Sunday before the game
    ],
)
def test_upcoming_sunday(now: datetime, expected: date) -> None:
    assert upcoming_sunday(now) == expected


def test_upcoming_sunday_converts_other_timezones() -> None:
    # 2026-10-18 14:30 UTC is 11:30 in Sao Paulo, still during the game.
    assert upcoming_sunday(datetime(2026, 10, 18, 14, 30, tzinfo=ZoneInfo("UTC"))) == SUNDAY


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(12, 0, 1), date(2026, 10, 11)),  # Monday before 08h: still last Sunday
        (at(12, 8, 0), SUNDAY),  # Monday 08h: the coming game
        (at(17, 23, 59), SUNDAY),  # Saturday night
        (at(18, 9, 59), SUNDAY),  # before the game
        (at(18, 12, 0), SUNDAY),  # the game is over, today stays in focus
        (at(18, 23, 59), SUNDAY),  # until the end of Sunday
        (at(19, 7, 59), SUNDAY),  # and Monday until 08h
        (at(19, 8, 0), date(2026, 10, 25)),  # then the next game
        (at(11, 13), date(2026, 10, 11)),  # the Sunday before keeps its own day after the game
        (at(11, 9), date(2026, 10, 11)),
    ],
)
def test_matchday_sunday(now: datetime, expected: date) -> None:
    assert matchday_sunday(now) == expected


def test_matchday_sunday_converts_other_timezones() -> None:
    # 2026-10-19 10:30 UTC is 07:30 in Sao Paulo, still Monday morning of the focus window.
    assert matchday_sunday(datetime(2026, 10, 19, 10, 30, tzinfo=ZoneInfo("UTC"))) == SUNDAY


@pytest.mark.parametrize(("goalkeepers", "line"), [(0, 22), (1, 21), (2, 20), (3, 20), (4, 20)])
def test_line_capacity(goalkeepers: int, line: int) -> None:
    assert line_capacity(goalkeepers) == line
    assert fits(line, goalkeepers)
    assert not fits(line + 1, goalkeepers)


def test_total_is_between_22_and_24() -> None:
    totals = {gk + line_capacity(gk) for gk in range(5)}
    assert totals == {22, 23, 24}


def test_at_most_four_goalkeepers() -> None:
    assert not fits(0, 5)
    with pytest.raises(ValueError, match="goalkeepers"):
        line_capacity(5)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(18, 13), SUNDAY),  # the game is over: today's Sunday
        (at(19, 7, 59), SUNDAY),
        (at(19, 8), SUNDAY),  # the result comes out and the Sunday is still there to read it
        (at(19, 23, 59), SUNDAY),
        (at(20, 0, 1), date(2026, 10, 25)),  # Tuesday: the next Sunday
    ],
)
def test_follow_sunday_keeps_the_game_all_monday(now: datetime, expected: date) -> None:
    assert follow_sunday(now) == expected
