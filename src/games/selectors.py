"""Read models for the list screens."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from accounts.models import Player
from games.domain import roster as engine
from games.domain.roster import Applicant, Entry, ListContext, Roster, waitlist_position
from games.domain.schedule import WeekSchedule
from games.domain.slots import MAX_GOALKEEPERS, line_capacity
from games.domain.types import EntryStatus, PlayerType, Position
from games.models import GameDay, ListEntry
from games.services import context_for, entry_rows, to_roster


class MyState(StrEnum):
    CANCELLED = "cancelado"
    NOT_OPEN = "fechada"  # the list has not opened for this player yet
    NOT_SIGNED_UP = "fora"
    ON_LIST = "lista"
    PROMOTED = "promovido"
    WAITING = "espera"
    CLOSED = "encerrada"  # the game started and the player is not on the list


@dataclass(frozen=True)
class Spot:
    player_id: int
    name: str
    is_me: bool
    promoted: bool
    bumped: bool
    player_type: PlayerType
    photo: str = ""


@dataclass(frozen=True)
class WeekView:
    game_day: GameDay
    schedule: WeekSchedule
    now: datetime
    state: MyState
    my_entry: Entry | None
    waitlist_position: int | None
    opens_at: datetime
    in_free_period: bool
    closed: bool
    line: tuple[Spot, ...]
    goalkeepers: tuple[Spot, ...]
    waiting_line: tuple[Spot, ...]
    waiting_goalkeepers: tuple[Spot, ...]
    line_capacity: int
    goalkeeper_capacity: int
    viewer_type: PlayerType
    joins_waitlist: bool = False  # joining now would put the viewer on the waitlist

    @property
    def can_join(self) -> bool:
        return self.state == MyState.NOT_SIGNED_UP

    @property
    def can_leave(self) -> bool:
        return not self.closed and self.state in {
            MyState.ON_LIST,
            MyState.PROMOTED,
            MyState.WAITING,
        }

    @property
    def leaving_is_fined(self) -> bool:
        return self.can_leave and self.state != MyState.WAITING and not self.in_free_period

    @property
    def total_confirmed(self) -> int:
        return len(self.line) + len(self.goalkeepers)

    @property
    def waiting_count(self) -> int:
        return len(self.waiting_line) + len(self.waiting_goalkeepers)


def _state(ctx: ListContext, entry: Entry | None, viewer_type: PlayerType) -> MyState:
    if ctx.cancelled:
        return MyState.CANCELLED
    if entry is not None and entry.status == EntryStatus.LISTA:
        return MyState.PROMOTED if entry.promoted else MyState.ON_LIST
    if entry is not None and entry.status == EntryStatus.ESPERA:
        return MyState.WAITING
    if ctx.closed:
        return MyState.CLOSED
    if not ctx.opened_for(viewer_type):
        return MyState.NOT_OPEN
    return MyState.NOT_SIGNED_UP


def build_week_view(game_day: GameDay, viewer: Player) -> WeekView:
    ctx = context_for(game_day)
    rows = entry_rows(game_day)
    roster = to_roster(rows)
    names = {row.player_id: row.player.name for row in rows}
    photos = {row.player_id: row.player.photo for row in rows}
    me = roster.get(viewer.pk)
    my_entry = me if me is not None and me.is_active else None
    viewer_type = PlayerType(viewer.type_in(game_day.date))
    opens_at = ctx.schedule.opens_for(viewer_type)
    free = ctx.in_free_period

    def spots(entries: tuple[Entry, ...]) -> tuple[Spot, ...]:
        return tuple(
            Spot(
                e.player_id,
                names[e.player_id],
                e.player_id == viewer.pk,
                e.promoted,
                e.bumped,
                e.player_type,
                photos[e.player_id],
            )
            for e in entries
        )

    state = _state(ctx, my_entry, viewer_type)
    applicant = Applicant(viewer.pk, Position(viewer.position), viewer_type)
    return WeekView(
        game_day=game_day,
        schedule=ctx.schedule,
        now=ctx.now,
        state=state,
        my_entry=my_entry,
        waitlist_position=waitlist_position(roster, viewer.pk, in_free_period=free),
        opens_at=opens_at,
        in_free_period=free,
        closed=ctx.closed,
        line=spots(roster.confirmed(Position.LINHA)),
        goalkeepers=spots(roster.confirmed(Position.GOLEIRO)),
        waiting_line=spots(roster.waitlist(Position.LINHA, in_free_period=free)),
        waiting_goalkeepers=spots(roster.waitlist(Position.GOLEIRO, in_free_period=free)),
        line_capacity=line_capacity(roster.count(Position.GOLEIRO)),
        goalkeeper_capacity=MAX_GOALKEEPERS,
        viewer_type=viewer_type,
        joins_waitlist=state == MyState.NOT_SIGNED_UP and _would_wait(roster, applicant, ctx),
    )


def _would_wait(roster: Roster, applicant: Applicant, ctx: ListContext) -> bool:
    """Ask the engine where a sign-up made right now would land, without saving anything."""
    outcome = engine.join(roster, applicant, ctx)
    return any(isinstance(event, engine.Waitlisted) for event in outcome.events)


@dataclass(frozen=True)
class BoardRoster:
    """Every sign-up of a Sunday for the diretoria, in engine order."""

    line: tuple[ListEntry, ...]
    goalkeepers: tuple[ListEntry, ...]
    waiting_line: tuple[ListEntry, ...]
    waiting_goalkeepers: tuple[ListEntry, ...]
    gone: tuple[ListEntry, ...]
    line_capacity: int

    @property
    def waiting_count(self) -> int:
        return len(self.waiting_line) + len(self.waiting_goalkeepers)


def board_roster(game_day: GameDay) -> BoardRoster:
    ctx = context_for(game_day)
    by_player = {row.player_id: row for row in entry_rows(game_day)}
    roster = to_roster(list(by_player.values()))

    def pick(entries: tuple[Entry, ...]) -> tuple[ListEntry, ...]:
        return tuple(by_player[e.player_id] for e in entries)

    free = ctx.in_free_period
    gone = sorted(
        (r for r in by_player.values() if r.status == EntryStatus.SAIU.value),
        key=lambda r: r.updated_at,
    )
    return BoardRoster(
        line=pick(roster.confirmed(Position.LINHA)),
        goalkeepers=pick(roster.confirmed(Position.GOLEIRO)),
        waiting_line=pick(roster.waitlist(Position.LINHA, in_free_period=free)),
        waiting_goalkeepers=pick(roster.waitlist(Position.GOLEIRO, in_free_period=free)),
        gone=tuple(gone),
        line_capacity=line_capacity(roster.count(Position.GOLEIRO)),
    )
