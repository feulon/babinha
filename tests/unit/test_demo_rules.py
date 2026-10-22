"""Demo mode (BABA_DEMO) releases the game day clock rules and nothing else."""

from datetime import date, datetime, time, timedelta

import pytest
from django.test import override_settings

from community.domain.voting import Phase, window
from core import demo
from core.context_processors import csrf_cookie, demo_banner
from games.domain.roster import (
    Applicant,
    DemoDay,
    Entry,
    ListClosed,
    ListContext,
    NotOpenYet,
    Roster,
    drop,
    join,
)
from games.domain.schedule import (
    WeekSchedule,
    last_game_date,
    matchday_sunday,
    teams_out,
    upcoming_sunday,
)
from games.domain.types import EntryStatus, PlayerType, Position
from tests.conftest import SP

WEEK = WeekSchedule(date(2026, 10, 18), list_opens_at=time(8, 0))
# Wednesday morning: the list is not open for agregados yet.
WEDNESDAY = datetime(2026, 10, 14, 9, 0, tzinfo=SP)
SUNDAY_AFTERNOON = datetime(2026, 10, 18, 15, 0, tzinfo=SP)
SUNDAY = date(2026, 10, 18)

M, A, G = PlayerType.MENSALISTA, PlayerType.AVULSO, PlayerType.AGREGADO
LINE = Position.LINHA


def context(now: datetime, day: DemoDay | None = None) -> ListContext:
    return ListContext(WEEK, now=now, demo=day)


def on_list(player_id: int, kind: PlayerType = A) -> Entry:
    return Entry(
        player_id, LINE, kind, EntryStatus.LISTA, joined_at=WEDNESDAY, confirmed_at=WEDNESDAY
    )


class TestBanner:
    @override_settings(BABA_DEMO=True)
    def test_shown_while_the_demo_is_on(self) -> None:
        assert demo_banner(None) == {"demo_banner": demo.BANNER}

    @override_settings(BABA_DEMO=False)
    def test_hidden_otherwise(self) -> None:
        assert demo_banner(None) == {"demo_banner": ""}


class TestCsrfCookieName:
    """The demo names its CSRF cookie apart from the regular app; the scripts need the name."""

    @override_settings(CSRF_COOKIE_NAME="baba_demo_csrftoken")
    def test_the_page_gets_the_demo_cookie_name(self) -> None:
        assert csrf_cookie(None) == {"csrf_cookie_name": "baba_demo_csrftoken"}

    @override_settings(CSRF_COOKIE_NAME="csrftoken")
    def test_the_page_gets_the_regular_cookie_name(self) -> None:
        assert csrf_cookie(None) == {"csrf_cookie_name": "csrftoken"}


class TestListContext:
    def test_the_real_calendar_decides_without_the_demo(self) -> None:
        ctx = context(WEDNESDAY)
        assert not ctx.opened_for(G)
        assert ctx.opened_for(M)
        assert not ctx.started
        assert not ctx.closed
        assert ctx.in_free_period

    def test_the_list_is_open_for_everyone_at_any_hour_in_the_demo(self) -> None:
        ctx = context(WEDNESDAY, DemoDay(teams_confirmed=False, finished=False))
        assert all(ctx.opened_for(kind) for kind in (M, A, G))
        outcome = join(Roster(), Applicant(1, LINE, G), ctx)
        assert outcome.roster.get(1).status == EntryStatus.LISTA

    def test_joining_stays_forbidden_without_the_demo(self) -> None:
        with pytest.raises(NotOpenYet):
            join(Roster(), Applicant(1, LINE, G), context(WEDNESDAY))

    def test_game_day_actions_are_open_before_kickoff_in_the_demo(self) -> None:
        assert context(WEDNESDAY, DemoDay(False, False)).started

    def test_the_free_period_lasts_until_the_teams_are_confirmed(self) -> None:
        before = context(SUNDAY_AFTERNOON, DemoDay(teams_confirmed=False, finished=False))
        after = context(WEDNESDAY, DemoDay(teams_confirmed=True, finished=False))
        assert before.in_free_period
        assert not after.in_free_period

    def test_dropping_out_after_the_teams_is_a_late_dropout(self) -> None:
        roster = Roster().put(on_list(1))
        late = drop(roster, 1, context(WEDNESDAY, DemoDay(teams_confirmed=True, finished=False)))
        free = drop(roster, 1, context(WEDNESDAY, DemoDay(teams_confirmed=False, finished=False)))
        assert late.events[0].late
        assert not free.events[0].late

    def test_the_list_closes_when_the_game_is_finished(self) -> None:
        open_ = context(SUNDAY_AFTERNOON, DemoDay(teams_confirmed=True, finished=False))
        closed = context(WEDNESDAY, DemoDay(teams_confirmed=True, finished=True))
        assert not open_.closed
        assert closed.closed
        with pytest.raises(ListClosed):
            join(Roster(), Applicant(1, LINE, A), closed)

    def test_capacity_still_sends_the_extra_player_to_the_waitlist(self) -> None:
        ctx = context(WEDNESDAY, DemoDay(False, False))
        roster = Roster()
        for player_id in range(1, 24):
            roster = join(roster, Applicant(player_id, LINE, A), ctx).roster
        assert roster.get(23).status == EntryStatus.ESPERA


class TestScheduleInDemo:
    @pytest.fixture(autouse=True)
    def demo_on(self, settings) -> None:
        settings.BABA_DEMO = True

    def test_the_sunday_does_not_roll_over_at_noon(self) -> None:
        assert upcoming_sunday(SUNDAY_AFTERNOON) == SUNDAY
        assert matchday_sunday(SUNDAY_AFTERNOON) == SUNDAY

    def test_the_demo_sunday_is_the_coming_one_all_week(self) -> None:
        monday_early = datetime(2026, 10, 19, 6, 0, tzinfo=SP)
        assert upcoming_sunday(monday_early) == date(2026, 10, 25)
        assert matchday_sunday(monday_early) == date(2026, 10, 25)

    def test_the_teams_are_out_at_any_hour(self) -> None:
        assert teams_out(WEDNESDAY)

    def test_the_coming_sunday_counts_as_played(self) -> None:
        assert last_game_date(WEDNESDAY) == SUNDAY


class TestScheduleWithoutDemo:
    def test_the_real_calendar_is_untouched(self) -> None:
        assert upcoming_sunday(SUNDAY_AFTERNOON) == date(2026, 10, 25)
        assert matchday_sunday(SUNDAY_AFTERNOON) == SUNDAY
        assert not teams_out(WEDNESDAY)
        assert last_game_date(WEDNESDAY) == WEDNESDAY.date()


class TestVotingWindowInDemo:
    def win(self, now: datetime, **kwargs: object):
        base = {
            "now": now,
            "closes_at": datetime(2026, 10, 19, 8, 0, tzinfo=SP),
            "has_game": True,
            "matches": 6,
        }
        return window(**{**base, **kwargs})

    def test_it_waits_for_the_games_even_after_sunday_noon(self) -> None:
        assert self.win(SUNDAY_AFTERNOON, game_over_at=None).phase == Phase.WAITING

    def test_ending_the_game_opens_it_with_a_match_unfinished(self) -> None:
        ended = SUNDAY_AFTERNOON - timedelta(hours=1)
        assert self.win(SUNDAY_AFTERNOON, game_over_at=ended).phase == Phase.OPEN

    def test_everyone_voting_closes_it_before_monday_morning(self) -> None:
        assert (
            self.win(
                SUNDAY_AFTERNOON,
                game_over_at=SUNDAY_AFTERNOON,
                everyone_voted=True,
            ).phase
            == Phase.CLOSED
        )
