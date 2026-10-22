from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from itertools import count
from zoneinfo import ZoneInfo

import pytest

from games.domain.roster import (
    AlreadyOnList,
    Applicant,
    Bumped,
    Confirmed,
    Dropped,
    Entry,
    GameCancelled,
    ListClosed,
    ListContext,
    NotOnList,
    NotOpenYet,
    Promoted,
    Roster,
    Waitlisted,
    drop,
    fill,
    join,
    waitlist_position,
)
from games.domain.schedule import WeekSchedule
from games.domain.types import EntryStatus, PlayerType, Position

SP = ZoneInfo("America/Sao_Paulo")
WEEK = WeekSchedule(date(2026, 10, 18), list_opens_at=time(8, 0))
TUESDAY = datetime(2026, 10, 13, 9, 0, tzinfo=SP)
THURSDAY = datetime(2026, 10, 15, 9, 0, tzinfo=SP)
# Week.tick() adds a minute before each operation, so the next one happens at 23h59.
BEFORE_SATURDAY_LAST_MINUTE = datetime(2026, 10, 17, 23, 58, tzinfo=SP)
SUNDAY_MORNING = datetime(2026, 10, 18, 8, 0, tzinfo=SP)

M, A, G = PlayerType.MENSALISTA, PlayerType.AVULSO, PlayerType.AGREGADO
LINE, GK = Position.LINHA, Position.GOLEIRO


@dataclass
class Week:
    """Drives the engine through a week, one minute per operation."""

    now: datetime = TUESDAY
    roster: Roster = field(default_factory=Roster)
    ids: count = field(default_factory=lambda: count(1))
    events: tuple = ()

    def ctx(self, *, cancelled: bool = False) -> ListContext:
        return ListContext(WEEK, self.now, cancelled=cancelled)

    def tick(self) -> None:
        self.now += timedelta(minutes=1)

    def join(self, position: Position = LINE, player_type: PlayerType = A, **kw) -> int:
        player_id = next(self.ids)
        self.join_as(player_id, position, player_type, **kw)
        return player_id

    def join_as(self, player_id: int, position: Position, player_type: PlayerType, **kw) -> None:
        self.tick()
        outcome = join(self.roster, Applicant(player_id, position, player_type), self.ctx(), **kw)
        self.roster, self.events = outcome.roster, outcome.events

    def join_many(self, n: int, position: Position = LINE, player_type: PlayerType = A) -> list:
        return [self.join(position, player_type) for _ in range(n)]

    def drop(self, player_id: int, **kw) -> None:
        self.tick()
        outcome = drop(self.roster, player_id, self.ctx(), **kw)
        self.roster, self.events = outcome.roster, outcome.events

    def status(self, player_id: int) -> EntryStatus:
        entry = self.roster.get(player_id)
        assert entry is not None
        return entry.status

    def counts(self) -> tuple[int, int]:
        return self.roster.count(LINE), self.roster.count(GK)

    def queue(self, position: Position = LINE) -> list[int]:
        free = self.ctx().in_free_period
        return [e.player_id for e in self.roster.waitlist(position, in_free_period=free)]


@pytest.fixture
def week() -> Week:
    return Week()


class TestOpening:
    def test_mensalistas_and_avulsos_join_from_monday_opening(self) -> None:
        w = Week(now=WEEK.opens_main - timedelta(minutes=1))
        for player_type in (M, A):
            w.join(player_type=player_type)
            assert w.events == (Confirmed(w.roster.entries[-1].player_id),)

    def test_before_monday_opening_is_rejected(self) -> None:
        w = Week(now=WEEK.opens_main - timedelta(minutes=2))
        with pytest.raises(NotOpenYet) as exc:
            w.join(player_type=M)
        assert exc.value.opens_at == WEEK.opens_main
        assert "12/10 às 08h00" in exc.value.message

    def test_agregado_waits_for_wednesday_20h(self, week: Week) -> None:
        with pytest.raises(NotOpenYet):
            week.join(player_type=G)
        week.now = WEEK.opens_agregado - timedelta(minutes=1)
        player = week.join(player_type=G)
        assert week.status(player) == EntryStatus.LISTA

    def test_agregado_with_no_vacancy_joins_the_waitlist(self, week: Week) -> None:
        week.join_many(22)
        week.now = WEEK.opens_agregado
        player = week.join(player_type=G)
        assert week.events == (Waitlisted(player),)

    def test_board_can_add_before_opening(self) -> None:
        w = Week(now=WEEK.opens_main - timedelta(hours=5))
        player = w.join(player_type=G, by_board=True)
        assert w.status(player) == EntryStatus.LISTA

    def test_closed_once_the_game_starts(self) -> None:
        w = Week(now=WEEK.starts - timedelta(minutes=1))
        with pytest.raises(ListClosed):
            w.join()

    def test_board_can_still_add_after_the_start(self) -> None:
        w = Week(now=WEEK.starts)
        player = w.join(by_board=True)
        assert w.status(player) == EntryStatus.LISTA

    def test_cancelled_sunday_rejects_everyone(self, week: Week) -> None:
        week.tick()
        with pytest.raises(GameCancelled):
            join(week.roster, Applicant(1, LINE, M), week.ctx(cancelled=True), by_board=True)

    def test_cannot_join_twice(self, week: Week) -> None:
        player = week.join()
        with pytest.raises(AlreadyOnList):
            week.join_as(player, LINE, A)

    def test_can_rejoin_after_dropping_out(self, week: Week) -> None:
        player = week.join()
        week.drop(player)
        week.join_as(player, LINE, A)
        assert week.status(player) == EntryStatus.LISTA


class TestSpots:
    def test_22_line_players_with_no_goalkeeper(self, week: Week) -> None:
        week.join_many(22)
        player = week.join()
        assert week.events == (Waitlisted(player),)
        assert week.counts() == (22, 0)

    def test_line_spots_shrink_as_goalkeepers_join(self, week: Week) -> None:
        week.join(GK)
        line = week.join_many(21)
        assert week.counts() == (21, 1)
        week.join(GK)  # takes back the 21st spot
        late_line = week.join()
        assert week.counts() == (20, 2)
        assert week.queue() == [line[-1], late_line]

    def test_up_to_four_goalkeepers(self, week: Week) -> None:
        week.join_many(4, GK)
        fifth = week.join(GK)
        assert week.events == (Waitlisted(fifth),)
        week.join_many(21)
        assert week.counts() == (20, 4)

    def test_total_never_exceeds_24(self, week: Week) -> None:
        for i in range(40):
            week.join(GK if i % 5 == 0 else LINE, (M, A)[i % 2])
        line, goalkeepers = week.counts()
        assert line + goalkeepers == 24

    def test_goalkeeper_spot_only_goes_to_a_goalkeeper(self, week: Week) -> None:
        keepers = week.join_many(4, GK)
        week.join_many(20)
        line_waiting = week.join()
        gk_waiting = week.join(GK)
        week.drop(keepers[0])
        assert week.events[-1] == Promoted(gk_waiting)
        assert week.status(line_waiting) == EntryStatus.ESPERA


class TestMensalistaPriority:
    def test_takes_the_spot_of_the_last_non_mensalista(self, week: Week) -> None:
        first_avulso = week.join(player_type=A)
        week.join_many(20, player_type=M)
        last_avulso = week.join(player_type=A)
        mensalista = week.join(player_type=M)
        assert week.events == (Bumped(last_avulso, by_player_id=mensalista), Confirmed(mensalista))
        assert week.status(first_avulso) == EntryStatus.LISTA
        assert week.status(last_avulso) == EntryStatus.ESPERA
        assert week.roster.get(last_avulso).bumped

    def test_bumped_player_goes_to_the_top_of_the_waitlist(self, week: Week) -> None:
        week.join_many(22, player_type=A)
        waiting = week.join(player_type=A)
        week.join(player_type=M)
        bumped = next(e for e in week.events if isinstance(e, Bumped)).player_id
        assert week.queue() == [bumped, waiting]
        assert waitlist_position(week.roster, bumped, in_free_period=True) == 1

    def test_agregado_can_be_bumped(self, week: Week) -> None:
        week.now = WEEK.opens_agregado
        week.join_many(21, player_type=M)
        agregado = week.join(player_type=G)
        week.join(player_type=M)
        assert week.status(agregado) == EntryStatus.ESPERA

    def test_waits_when_every_spot_is_a_mensalista(self, week: Week) -> None:
        week.join_many(22, player_type=M)
        player = week.join(player_type=M)
        assert week.events == (Waitlisted(player),)

    def test_goalkeeper_mensalista_bumps_only_goalkeepers(self, week: Week) -> None:
        keepers = week.join_many(4, GK, A)
        week.join_many(20, LINE, A)
        mensalista_gk = week.join(GK, M)
        assert week.events == (Bumped(keepers[-1], mensalista_gk), Confirmed(mensalista_gk))
        assert week.counts() == (20, 4)

    def test_line_mensalista_does_not_bump_goalkeepers(self, week: Week) -> None:
        week.join_many(4, GK, A)
        week.join_many(20, LINE, M)
        player = week.join(LINE, M)
        assert week.events == (Waitlisted(player),)

    def test_priority_holds_until_saturday_23h59(self, week: Week) -> None:
        week.join_many(22, player_type=A)
        week.now = BEFORE_SATURDAY_LAST_MINUTE
        week.join(player_type=M)
        assert isinstance(week.events[0], Bumped)

    def test_no_priority_after_saturday(self, week: Week) -> None:
        week.join_many(22, player_type=A)
        week.now = SUNDAY_MORNING
        player = week.join(player_type=M)
        assert week.events == (Waitlisted(player),)

    def test_waiting_mensalista_is_promoted_first_during_free_period(self, week: Week) -> None:
        week.join_many(21, player_type=M)
        avulso = week.join(player_type=A)
        week.join(player_type=M)  # bumps the avulso
        waiting_mensalista = week.join(player_type=M)
        assert week.queue() == [waiting_mensalista, avulso]
        week.drop(1)
        assert week.events[-1] == Promoted(waiting_mensalista)


class TestGoalkeeperFlexPriority:
    def test_goalkeeper_takes_back_a_flex_spot_from_the_last_line_player(self, week: Week) -> None:
        line = week.join_many(22, player_type=A)
        goalkeeper = week.join(GK)
        assert week.events == (Bumped(line[-1], goalkeeper), Confirmed(goalkeeper))
        assert week.counts() == (21, 1)
        assert week.queue() == [line[-1]]

    def test_second_goalkeeper_takes_the_other_flex_spot(self, week: Week) -> None:
        line = week.join_many(22, player_type=A)
        week.join(GK)
        week.join(GK)
        assert week.counts() == (20, 2)
        assert week.queue() == [line[-2], line[-1]]

    def test_spares_mensalistas_when_possible(self, week: Week) -> None:
        avulsos = week.join_many(2, player_type=A)
        week.join_many(20, player_type=M)
        goalkeeper = week.join(GK)
        assert week.events[0] == Bumped(avulsos[-1], goalkeeper)

    def test_bumps_the_last_mensalista_when_all_are_mensalistas(self, week: Week) -> None:
        line = week.join_many(22, player_type=M)
        goalkeeper = week.join(GK)
        assert week.events[0] == Bumped(line[-1], goalkeeper)

    def test_third_goalkeeper_needs_no_room(self, week: Week) -> None:
        week.join_many(2, GK)
        week.join_many(20)
        week.join(GK)
        assert week.counts() == (20, 3)
        assert week.queue() == []

    def test_no_displacement_after_saturday(self, week: Week) -> None:
        week.join_many(22)
        week.now = SUNDAY_MORNING
        goalkeeper = week.join(GK)
        assert week.events == (Waitlisted(goalkeeper),)
        assert week.queue(GK) == [goalkeeper]

    def test_line_player_leaving_lets_a_waiting_goalkeeper_in(self, week: Week) -> None:
        line = week.join_many(22)
        week.now = SUNDAY_MORNING
        goalkeeper = week.join(GK)
        week.drop(line[0])
        assert week.events[-1] == Promoted(goalkeeper)
        assert week.counts() == (21, 1)


class TestDropping:
    def test_free_until_saturday_23h59(self, week: Week) -> None:
        player = week.join()
        week.now = BEFORE_SATURDAY_LAST_MINUTE
        week.drop(player)
        assert week.events == (Dropped(player, was_confirmed=True, late=False),)

    def test_late_after_saturday(self, week: Week) -> None:
        player = week.join()
        week.now = SUNDAY_MORNING
        week.drop(player)
        assert week.events == (Dropped(player, was_confirmed=True, late=True),)

    def test_leaving_the_waitlist_is_never_late(self, week: Week) -> None:
        week.join_many(22)
        waiting = week.join()
        week.now = SUNDAY_MORNING
        week.drop(waiting)
        assert week.events == (Dropped(waiting, was_confirmed=False, late=False),)

    def test_board_removal_is_never_late(self, week: Week) -> None:
        player = week.join()
        week.now = WEEK.starts + timedelta(minutes=5)
        week.drop(player, by_board=True)
        assert week.events == (Dropped(player, was_confirmed=True, late=False),)

    def test_players_cannot_drop_after_the_start(self, week: Week) -> None:
        player = week.join()
        week.now = WEEK.starts
        with pytest.raises(ListClosed):
            week.drop(player)

    def test_cannot_drop_when_not_on_list(self, week: Week) -> None:
        with pytest.raises(NotOnList):
            week.drop(99)

    def test_cancelled_sunday_blocks_player_drops(self, week: Week) -> None:
        player = week.join()
        with pytest.raises(GameCancelled):
            drop(week.roster, player, week.ctx(cancelled=True))


class TestWaitlist:
    def test_first_come_first_served(self, week: Week) -> None:
        line = week.join_many(22)
        waiting = week.join_many(3)
        week.drop(line[5])
        assert week.events == (Dropped(line[5], True, False), Promoted(waiting[0]))
        assert week.queue() == waiting[1:]
        promoted = week.roster.get(waiting[0])
        assert promoted.promoted
        assert promoted.status == EntryStatus.LISTA

    def test_promoted_player_may_decline_free_until_saturday(self, week: Week) -> None:
        line = week.join_many(22)
        waiting = week.join()
        week.drop(line[0])
        week.now = BEFORE_SATURDAY_LAST_MINUTE
        week.drop(waiting)
        assert week.events[0] == Dropped(waiting, was_confirmed=True, late=False)

    def test_promotion_continues_after_saturday_and_declining_is_late(self, week: Week) -> None:
        line = week.join_many(22)
        waiting = week.join()
        week.now = SUNDAY_MORNING
        week.drop(line[0])
        assert week.events == (Dropped(line[0], True, True), Promoted(waiting))
        week.drop(waiting)
        assert week.events[0] == Dropped(waiting, was_confirmed=True, late=True)

    def test_no_promotion_once_the_game_started(self, week: Week) -> None:
        line = week.join_many(22)
        waiting = week.join()
        week.now = WEEK.starts
        week.drop(line[0], by_board=True)
        assert week.status(waiting) == EntryStatus.ESPERA

    def test_goalkeeper_leaving_frees_a_line_spot(self, week: Week) -> None:
        keepers = week.join_many(2, GK)
        week.join_many(20)
        waiting = week.join()
        week.drop(keepers[0])
        assert week.events[-1] == Promoted(waiting)
        assert week.counts() == (21, 1)

    def test_flex_spot_after_saturday_goes_to_who_waited_longest(self, week: Week) -> None:
        week.join(GK)
        line = week.join_many(21)
        week.now = SUNDAY_MORNING
        line_waiting = week.join()
        gk_waiting = week.join(GK)
        week.drop(line[0])
        assert week.events[-1] == Promoted(line_waiting)
        assert week.status(gk_waiting) == EntryStatus.ESPERA

    def test_flex_spot_during_free_period_goes_to_a_waiting_goalkeeper(self) -> None:
        def entry(player_id: int, position: Position, status: EntryStatus) -> Entry:
            confirmed = TUESDAY if status == EntryStatus.LISTA else None
            return Entry(player_id, position, A, status, TUESDAY, confirmed_at=confirmed)

        roster = Roster(
            (
                entry(1, GK, EntryStatus.LISTA),
                *(entry(i, LINE, EntryStatus.LISTA) for i in range(10, 30)),
                entry(2, LINE, EntryStatus.ESPERA),
                entry(3, GK, EntryStatus.ESPERA),
            )
        )
        # One goalkeeper and 20 line players: the 21st spot could go to either queue head.
        outcome = fill(roster, ListContext(WEEK, THURSDAY))
        assert outcome.events == (Promoted(3),)
        assert outcome.roster.count(GK) == 2
        assert outcome.roster.count(LINE) == 20

    def test_waitlist_position_is_per_position(self, week: Week) -> None:
        week.join_many(4, GK)
        week.join_many(20)
        line_waiting = week.join()
        gk_waiting = week.join(GK)
        assert waitlist_position(week.roster, line_waiting, in_free_period=True) == 1
        assert waitlist_position(week.roster, gk_waiting, in_free_period=True) == 1
        assert waitlist_position(week.roster, 1, in_free_period=True) is None
