"""The weekly list engine: joining, dropping out and the automatic waitlist.

Pure functions over immutable values; persistence and locking live in `games.services`.

Rules (docs/plan.md "Lista semanal" plus the captain's decisions):
- The list opens Monday morning for mensalistas and avulsos, Wednesday 20h for agregados.
- Spots: see `games.domain.slots`. Anyone arriving with no spot for their position waits.
- Until Saturday 23h59 (the free period) two priorities apply, and whoever loses the spot goes
  to the top of the waitlist with no fine:
  - a mensalista takes the spot of the last non-mensalista to enter the list in their position;
  - the 1st and 2nd goalkeeper take back a flex spot from the last line player to enter
    (last non-mensalista first).
- The waitlist is first come, first served per position (a goalkeeper spot only goes to a
  goalkeeper). Bumped players are on top; during the free period, waiting mensalistas go first.
  Promotion is automatic until the game starts.
- Dropping out (or declining a promotion) is free until Saturday 23h59; afterwards leaving a
  spot is a late dropout, which the service turns into a fine. Leaving the waitlist is free.
"""

from dataclasses import dataclass, replace
from datetime import datetime

from games.domain.schedule import WeekSchedule
from games.domain.slots import MAX_GOALKEEPERS, fits, line_capacity
from games.domain.types import EntryStatus, PlayerType, Position


class ListError(Exception):
    """A list operation that is not allowed. `message` is shown to the user (pt-BR)."""

    message = "Operação não permitida na lista."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message


class GameCancelled(ListError):
    message = "Este domingo foi cancelado pela diretoria."


class ListClosed(ListError):
    message = "A lista está fechada: o jogo já começou."


class NotOpenYet(ListError):
    def __init__(self, opens_at: datetime) -> None:
        self.opens_at = opens_at
        super().__init__(f"A lista ainda não abriu para você. Abre em {opens_at:%d/%m às %Hh%M}.")


class AlreadyOnList(ListError):
    message = "Você já está na lista ou na espera."


class NotOnList(ListError):
    message = "Você não está na lista nem na espera."


@dataclass(frozen=True)
class Applicant:
    player_id: int
    position: Position
    player_type: PlayerType


@dataclass(frozen=True)
class Entry:
    player_id: int
    position: Position
    player_type: PlayerType
    status: EntryStatus
    joined_at: datetime  # arrival in the queue; orders the waitlist
    confirmed_at: datetime | None = None  # when the current spot was obtained
    bumped: bool = False  # lost the spot to a priority rule and waits on top of the queue
    promoted: bool = False  # got the current spot from the waitlist

    @property
    def is_mensalista(self) -> bool:
        return self.player_type == PlayerType.MENSALISTA

    @property
    def is_active(self) -> bool:
        return self.status != EntryStatus.SAIU


@dataclass(frozen=True)
class DemoDay:
    """Demo mode: the calendar is released, so the state of the Sunday stands in for it."""

    teams_confirmed: bool  # ends the free period, as Saturday 23h59 does
    finished: bool  # closes the list, as the kickoff does


@dataclass(frozen=True)
class ListContext:
    schedule: WeekSchedule
    now: datetime
    cancelled: bool = False
    demo: DemoDay | None = None

    @property
    def in_free_period(self) -> bool:
        if self.demo is not None:
            return not self.demo.teams_confirmed
        return self.now < self.schedule.free_until

    @property
    def closed(self) -> bool:
        if self.demo is not None:
            return self.demo.finished
        return self.now >= self.schedule.starts

    @property
    def started(self) -> bool:
        """The game began: presence, goals and the end of the game can be recorded."""
        return self.demo is not None or self.now >= self.schedule.starts

    def opened_for(self, player_type: PlayerType) -> bool:
        return self.demo is not None or self.now >= self.schedule.opens_for(player_type)


# Events describe what happened so the caller can persist and report it.
@dataclass(frozen=True)
class Confirmed:
    player_id: int


@dataclass(frozen=True)
class Waitlisted:
    player_id: int


@dataclass(frozen=True)
class Bumped:
    player_id: int
    by_player_id: int


@dataclass(frozen=True)
class Promoted:
    player_id: int


@dataclass(frozen=True)
class Dropped:
    player_id: int
    was_confirmed: bool
    late: bool  # left a spot after the free period: fined unless the diretoria removed them


Event = Confirmed | Waitlisted | Bumped | Promoted | Dropped


@dataclass(frozen=True)
class Roster:
    entries: tuple[Entry, ...] = ()

    def get(self, player_id: int) -> Entry | None:
        return next((e for e in self.entries if e.player_id == player_id), None)

    def confirmed(self, position: Position) -> tuple[Entry, ...]:
        """Players holding a spot, in the order they got it."""
        holders = [
            e for e in self.entries if e.status == EntryStatus.LISTA and e.position == position
        ]
        return tuple(sorted(holders, key=lambda e: (e.confirmed_at, e.player_id)))

    def waitlist(self, position: Position, *, in_free_period: bool) -> tuple[Entry, ...]:
        waiting = [
            e for e in self.entries if e.status == EntryStatus.ESPERA and e.position == position
        ]
        return tuple(sorted(waiting, key=lambda e: _queue_key(e, in_free_period)))

    def count(self, position: Position) -> int:
        return len(self.confirmed(position))

    def put(self, entry: Entry) -> "Roster":
        others = tuple(e for e in self.entries if e.player_id != entry.player_id)
        return Roster((*others, entry))

    def within_capacity(self) -> bool:
        return fits(self.count(Position.LINHA), self.count(Position.GOLEIRO))


@dataclass(frozen=True)
class Outcome:
    roster: Roster
    events: tuple[Event, ...]


def _queue_key(entry: Entry, in_free_period: bool) -> tuple[int, int, datetime, int]:
    mensalista_first = 0 if in_free_period and entry.is_mensalista else 1
    bumped_first = 0 if entry.bumped else 1
    return (mensalista_first, bumped_first, entry.joined_at, entry.player_id)


def _confirm(entry: Entry, now: datetime, *, promoted: bool) -> Entry:
    return replace(
        entry, status=EntryStatus.LISTA, confirmed_at=now, bumped=False, promoted=promoted
    )


def _bump(entry: Entry) -> Entry:
    return replace(entry, status=EntryStatus.ESPERA, confirmed_at=None, bumped=True, promoted=False)


def _last_to_enter(entries: tuple[Entry, ...], *, non_mensalista_only: bool) -> Entry | None:
    candidates = [e for e in entries if not (non_mensalista_only and e.is_mensalista)]
    return candidates[-1] if candidates else None


def _goalkeeper_placement(
    roster: Roster, applicant: Applicant, ctx: ListContext
) -> tuple[bool, Entry | None]:
    """(gets a spot, entry bumped to make room)."""
    goalkeepers = roster.count(Position.GOLEIRO)
    line = roster.count(Position.LINHA)
    if goalkeepers < MAX_GOALKEEPERS:
        if line <= line_capacity(goalkeepers + 1):
            return True, None
        if ctx.in_free_period:
            holders = roster.confirmed(Position.LINHA)
            victim = _last_to_enter(holders, non_mensalista_only=True) or holders[-1]
            return True, victim
        return False, None
    if applicant.player_type == PlayerType.MENSALISTA and ctx.in_free_period:
        victim = _last_to_enter(roster.confirmed(Position.GOLEIRO), non_mensalista_only=True)
        return victim is not None, victim
    return False, None


def _line_placement(
    roster: Roster, applicant: Applicant, ctx: ListContext
) -> tuple[bool, Entry | None]:
    if roster.count(Position.LINHA) < line_capacity(roster.count(Position.GOLEIRO)):
        return True, None
    if applicant.player_type == PlayerType.MENSALISTA and ctx.in_free_period:
        victim = _last_to_enter(roster.confirmed(Position.LINHA), non_mensalista_only=True)
        return victim is not None, victim
    return False, None


def join(
    roster: Roster, applicant: Applicant, ctx: ListContext, *, by_board: bool = False
) -> Outcome:
    """Put the applicant on the list, or on the waitlist when there is no spot for them.

    `by_board` is the diretoria editing the list: opening times and the game start do not apply.
    """
    if ctx.cancelled:
        raise GameCancelled
    if not by_board:
        if ctx.closed:
            raise ListClosed
        if not ctx.opened_for(applicant.player_type):
            raise NotOpenYet(ctx.schedule.opens_for(applicant.player_type))
    existing = roster.get(applicant.player_id)
    if existing is not None and existing.is_active:
        raise AlreadyOnList

    entry = Entry(
        player_id=applicant.player_id,
        position=applicant.position,
        player_type=applicant.player_type,
        status=EntryStatus.ESPERA,
        joined_at=ctx.now,
    )
    placement = _goalkeeper_placement if applicant.position == Position.GOLEIRO else _line_placement
    gets_spot, victim = placement(roster, applicant, ctx)

    if not gets_spot:
        return Outcome(roster.put(entry), (Waitlisted(applicant.player_id),))

    events: list[Event] = []
    if victim is not None:
        roster = roster.put(_bump(victim))
        events.append(Bumped(victim.player_id, by_player_id=applicant.player_id))
    roster = roster.put(_confirm(entry, ctx.now, promoted=False))
    events.append(Confirmed(applicant.player_id))
    _check_capacity(roster)
    return Outcome(roster, tuple(events))


def drop(roster: Roster, player_id: int, ctx: ListContext, *, by_board: bool = False) -> Outcome:
    """Leave the list or the waitlist, then refill freed spots from the waitlist.

    `by_board` is the diretoria removing someone: allowed any time and never late.
    """
    if not by_board:
        if ctx.cancelled:
            raise GameCancelled
        if ctx.closed:
            raise ListClosed
    entry = roster.get(player_id)
    if entry is None or not entry.is_active:
        raise NotOnList

    was_confirmed = entry.status == EntryStatus.LISTA
    late = was_confirmed and not ctx.in_free_period and not by_board
    left = replace(entry, status=EntryStatus.SAIU, confirmed_at=None, bumped=False, promoted=False)
    dropped = Dropped(player_id, was_confirmed=was_confirmed, late=late)
    refilled = fill(roster.put(left), ctx)
    return Outcome(refilled.roster, (dropped, *refilled.events))


def fill(roster: Roster, ctx: ListContext) -> Outcome:
    """Promote from the waitlist while spots are free. Stops once the game starts.

    When a free spot could go to either position (a flex spot), goalkeepers win during the free
    period; afterwards whoever has waited longest gets it.
    """
    if ctx.cancelled or ctx.closed:
        return Outcome(roster, ())
    events: list[Event] = []
    while (head := _next_promotion(roster, ctx)) is not None:
        roster = roster.put(_confirm(head, ctx.now, promoted=True))
        events.append(Promoted(head.player_id))
    _check_capacity(roster)
    return Outcome(roster, tuple(events))


def _next_promotion(roster: Roster, ctx: ListContext) -> Entry | None:
    goalkeepers = roster.count(Position.GOLEIRO)
    line = roster.count(Position.LINHA)
    free = ctx.in_free_period
    candidates: list[Entry] = []

    gk_queue = roster.waitlist(Position.GOLEIRO, in_free_period=free)
    if gk_queue and goalkeepers < MAX_GOALKEEPERS and line <= line_capacity(goalkeepers + 1):
        if free:
            return gk_queue[0]
        candidates.append(gk_queue[0])

    line_queue = roster.waitlist(Position.LINHA, in_free_period=free)
    if line_queue and line < line_capacity(goalkeepers):
        candidates.append(line_queue[0])

    if not candidates:
        return None
    return min(candidates, key=lambda e: _queue_key(e, free))


def _check_capacity(roster: Roster) -> None:
    if not roster.within_capacity():
        raise AssertionError("list engine exceeded the spot limits")


def waitlist_position(roster: Roster, player_id: int, *, in_free_period: bool) -> int | None:
    """1-based place in the waitlist of the player's position, or None if not waiting."""
    entry = roster.get(player_id)
    if entry is None or entry.status != EntryStatus.ESPERA:
        return None
    queue = roster.waitlist(entry.position, in_free_period=in_free_period)
    return next(i for i, e in enumerate(queue, start=1) if e.player_id == player_id)
