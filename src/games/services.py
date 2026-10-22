"""List operations: load the Sunday under a row lock, run the engine, persist the result.

Every operation that changes a Sunday's list first takes `SELECT ... FOR UPDATE` on its
GameDay row, so concurrent sign-ups are serialized and the spot limits can never be exceeded.
"""

from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Exists, OuterRef

from accounts.models import MonthlyEnrollment, Player, month_start
from core import clock, demo
from core.models import LATE_TIERS
from finance import charges
from fines import services as fines
from fines.models import Fine, FineReason
from games.domain import roster as engine
from games.domain.roster import Applicant, DemoDay, Entry, ListContext, ListError, Roster
from games.domain.schedule import WeekSchedule, follow_sunday, matchday_sunday, upcoming_sunday
from games.domain.types import EntryStatus, PlayerType, Position
from games.models import Attendance, GameDay, ListEntry


@dataclass(frozen=True)
class ListResult:
    events: tuple[engine.Event, ...]
    fines: tuple[Fine, ...] = ()


PAID_LATE_FINE_MESSAGE = (
    "A multa por atraso deste jogador já foi paga. O atraso não pode mais mudar."
)


def schedule_for(sunday: date) -> WeekSchedule:
    return WeekSchedule(sunday, list_opens_at=settings.BABA_LIST_OPENS_AT)


def sunday_over(game_day: GameDay, now=None) -> bool:
    """The Sunday has ended: the diretoria finished it, or its fees opened (12h30, which is also
    when the Bola de Ouro opens), whether or not `finished_at` has been saved yet."""
    if game_day.is_cancelled:
        return False
    if game_day.is_finished:
        return True
    now = now or clock.now()
    return not demo.is_on() and now >= schedule_for(game_day.date).charges_open


def game_day_for(sunday: date) -> GameDay:
    game_day, _ = GameDay.objects.get_or_create(date=sunday)
    return game_day


def current_game_day() -> GameDay:
    return game_day_for(upcoming_sunday(clock.now()))


def matchday_game_day() -> GameDay:
    """The Sunday of the game day screens, which stays on today's game after it ends."""
    return game_day_for(matchday_sunday(clock.now()))


def follow_game_day() -> GameDay:
    """The Sunday of the player's follow screen (see `follow_sunday`)."""
    return game_day_for(follow_sunday(clock.now()))


def context_for(game_day: GameDay) -> ListContext:
    return ListContext(
        schedule_for(game_day.date),
        now=clock.now(),
        cancelled=game_day.is_cancelled,
        demo=_demo_day(game_day) if demo.is_on() else None,
    )


def _demo_day(game_day: GameDay) -> DemoDay:
    teams_confirmed = GameDay.objects.filter(
        pk=game_day.pk, lineup__confirmed_at__isnull=False
    ).exists()
    return DemoDay(teams_confirmed=teams_confirmed, finished=game_day.is_finished)


def game_started(game_day: GameDay) -> bool:
    """Presence, goals and the end of the game open at kickoff (always, in demo mode)."""
    return context_for(game_day).started


def entry_rows(game_day: GameDay) -> list[ListEntry]:
    """The Sunday's sign-ups; `enrolled` tells who is a mensalista in the Sunday's month."""
    enrolled = MonthlyEnrollment.objects.filter(
        player=OuterRef("player_id"), month=month_start(game_day.date)
    )
    return list(
        ListEntry.objects.filter(game_day=game_day)
        .select_related("player")
        .annotate(enrolled=Exists(enrolled))
    )


def to_roster(rows: list[ListEntry]) -> Roster:
    return Roster(tuple(_to_domain(row) for row in rows))


def load_roster(game_day: GameDay) -> Roster:
    return to_roster(entry_rows(game_day))


def _to_domain(row: ListEntry) -> Entry:
    return Entry(
        player_id=row.player_id,
        position=Position(row.position),
        # Monthly priority uses the Sunday's month, even when revisiting an older list.
        player_type=PlayerType.MENSALISTA if row.enrolled else PlayerType(row.player.player_type),
        status=EntryStatus(row.status),
        joined_at=row.joined_at,
        confirmed_at=row.confirmed_at,
        bumped=row.bumped,
        promoted=row.promoted,
    )


def _persist(game_day: GameDay, before: Roster, after: Roster) -> set[int]:
    """Write the entries that changed and return their player ids."""
    changed = set()
    for entry in after.entries:
        if before.get(entry.player_id) == entry:
            continue
        changed.add(entry.player_id)
        ListEntry.objects.update_or_create(
            game_day=game_day,
            player_id=entry.player_id,
            defaults={
                "position": entry.position.value,
                "status": entry.status.value,
                "joined_at": entry.joined_at,
                "confirmed_at": entry.confirmed_at,
                "bumped": entry.bumped,
                "promoted": entry.promoted,
            },
        )
    return changed


def _apply(game_day: GameDay, before: Roster, after: Roster, actor: Player | None = None) -> None:
    """Persist the new roster and bring the Sunday charges of whoever changed in line."""
    changed = _persist(game_day, before, after)
    charges.sync_game_charges(game_day, changed, actor=actor)


def _lock(game_day_id: int) -> GameDay:
    return GameDay.objects.select_for_update().get(pk=game_day_id)


def _require_board(actor: Player | None) -> None:
    if actor is not None and not actor.is_board:
        raise PermissionDenied


@transaction.atomic
def join_list(game_day_id: int, player: Player, *, board_actor: Player | None = None) -> ListResult:
    """Sign `player` up. With `board_actor` the diretoria is adding them by hand."""
    _require_board(board_actor)
    if not player.is_active:
        raise ListError("Jogador inativo.")
    game_day = _lock(game_day_id)
    before = load_roster(game_day)
    applicant = Applicant(
        player.pk, Position(player.position), PlayerType(player.type_in(game_day.date))
    )
    outcome = engine.join(
        before, applicant, context_for(game_day), by_board=board_actor is not None
    )
    _apply(game_day, before, outcome.roster, board_actor)
    return ListResult(outcome.events)


@transaction.atomic
def leave_list(
    game_day_id: int, player: Player, *, board_actor: Player | None = None
) -> ListResult:
    """Drop out, decline a promotion or leave the waitlist. A late dropout records a fine.

    With `board_actor` the diretoria is removing the player, which is never fined.
    """
    _require_board(board_actor)
    game_day = _lock(game_day_id)
    before = load_roster(game_day)
    outcome = engine.drop(
        before, player.pk, context_for(game_day), by_board=board_actor is not None
    )
    _apply(game_day, before, outcome.roster, board_actor)
    recorded = tuple(
        fines.record_fine(player, game_day, FineReason.LATE_DROPOUT)
        for event in outcome.events
        if isinstance(event, engine.Dropped) and event.late
    )
    return ListResult(outcome.events, recorded)


@transaction.atomic
def cancel_game_day(game_day_id: int, actor: Player, reason: str) -> int:
    """Cancel the Sunday, its unpaid charges and its pending fines. Returns how many fines were
    voided. Nothing already paid is refunded automatically."""
    _require_board(actor)
    game_day = _lock(game_day_id)
    if game_day.is_cancelled:
        raise ListError("Este domingo já está cancelado.")
    game_day.cancelled_at = clock.now()
    game_day.cancelled_by = actor
    game_day.cancel_reason = reason.strip()
    game_day.save(update_fields=["cancelled_at", "cancelled_by", "cancel_reason"])
    charges.sync_game_charges(game_day, actor=actor)
    return fines.void_for_game_day(game_day, actor)


@transaction.atomic
def reopen_game_day(game_day_id: int, actor: Player) -> ListResult:
    """Undo a cancellation. Voided fines stay voided; free spots are refilled from the queue and
    whoever holds a spot is charged again."""
    _require_board(actor)
    game_day = _lock(game_day_id)
    if not game_day.is_cancelled:
        raise ListError("Este domingo não está cancelado.")
    game_day.cancelled_at = None
    game_day.cancelled_by = None
    game_day.cancel_reason = ""
    game_day.save(update_fields=["cancelled_at", "cancelled_by", "cancel_reason"])
    before = load_roster(game_day)
    outcome = engine.fill(before, context_for(game_day))
    _persist(game_day, before, outcome.roster)
    charges.sync_game_charges(game_day, actor=actor)
    return ListResult(outcome.events)


@transaction.atomic
def finish_game_day(game_day_id: int, actor: Player) -> bool:
    """The diretoria ends the game before 12h30: everyone on the list now owes the single fee.
    Returns False when the game was already finished."""
    _require_board(actor)
    game_day = _lock(game_day_id)
    if game_day.is_cancelled:
        raise ListError("Este domingo foi cancelado.")
    if not game_started(game_day):
        raise ListError("O jogo só pode ser finalizado a partir do início.")
    return charges.finish_game_day(game_day, actor)


def _lock_for_attendance(game_day_id: int, actor: Player) -> GameDay:
    _require_board(actor)
    game_day = _lock(game_day_id)
    if game_day.is_cancelled:
        raise ListError("Este domingo foi cancelado.")
    if not game_started(game_day):
        raise ListError("A presença só pode ser marcada a partir do início do jogo.")
    return game_day


@transaction.atomic
def mark_attendance(
    game_day_id: int, player: Player, attendance: Attendance | str, actor: Player
) -> Fine | None:
    """Record presence for someone on the list. Marking a no-show records a fine; correcting it
    back voids that fine. Returns the fine recorded, if any."""
    game_day = _lock_for_attendance(game_day_id, actor)
    if attendance not in {"", *Attendance.values}:
        raise ListError("Valor de presença inválido.")
    try:
        entry = ListEntry.objects.select_for_update().get(
            game_day=game_day, player=player, status=EntryStatus.LISTA.value
        )
    except ListEntry.DoesNotExist as exc:
        raise ListError("Este jogador não está na lista deste domingo.") from exc

    if (
        entry.late_minutes is not None
        and attendance != Attendance.PRESENTE
        and fines.has_paid_late_fine(player, game_day)
    ):
        raise ListError(PAID_LATE_FINE_MESSAGE)

    previous = entry.attendance
    entry.attendance = attendance
    entry.attendance_marked_by = actor if attendance else None
    entry.attendance_marked_at = clock.now() if attendance else None
    # Lateness only makes sense for someone who came: any other mark clears it.
    lateness_dropped = entry.late_minutes is not None and attendance != Attendance.PRESENTE
    if lateness_dropped:
        entry.late_minutes = None
    entry.save(
        update_fields=["attendance", "attendance_marked_by", "attendance_marked_at", "late_minutes"]
    )

    if lateness_dropped:
        fines.void_late(player, game_day, actor)
    if previous == Attendance.FALTOU and attendance != Attendance.FALTOU:
        fines.void_no_show(player, game_day, actor)
    if (
        attendance == Attendance.FALTOU
        and previous != Attendance.FALTOU
        and not fines.has_no_show(player, game_day)
    ):
        return fines.record_fine(player, game_day, FineReason.NO_SHOW)
    return None


@transaction.atomic
def mark_late(game_day_id: int, player: Player, minutes: int | None, actor: Player) -> None:
    """Mark how late someone arrived (15 or 30 min), or clear it with None. Arriving means being
    present, so a player with no mark yet becomes present. A late fine already recorded for
    another tier is voided: the diretoria fines again once the mark is right."""
    game_day = _lock_for_attendance(game_day_id, actor)
    if minutes is not None and minutes not in LATE_TIERS:
        raise ListError("Valor de atraso inválido.")
    try:
        entry = ListEntry.objects.select_for_update().get(
            game_day=game_day, player=player, status=EntryStatus.LISTA.value
        )
    except ListEntry.DoesNotExist as exc:
        raise ListError("Este jogador não está na lista deste domingo.") from exc
    if entry.attendance == Attendance.FALTOU:
        raise ListError("Quem faltou não pode ter atraso. Corrija a presença antes.")

    if entry.late_minutes != minutes and fines.has_paid_late_fine(player, game_day):
        raise ListError(PAID_LATE_FINE_MESSAGE)

    previous = entry.late_minutes
    entry.late_minutes = minutes
    fields = ["late_minutes"]
    if minutes is not None and not entry.attendance:
        entry.attendance = Attendance.PRESENTE.value
        entry.attendance_marked_by = actor
        entry.attendance_marked_at = clock.now()
        fields += ["attendance", "attendance_marked_by", "attendance_marked_at"]
    entry.save(update_fields=fields)
    if previous != minutes:
        fines.void_late(player, game_day, actor)


@transaction.atomic
def fine_late_arrivals(game_day_id: int, actor: Player) -> list[Fine]:
    """Fine everyone marked late who has no late fine yet. Returns the fines recorded."""
    game_day = _lock_for_attendance(game_day_id, actor)
    entries = (
        ListEntry.objects.select_for_update()
        .filter(game_day=game_day, status=EntryStatus.LISTA.value, late_minutes__isnull=False)
        .select_related("player")
        .order_by("confirmed_at", "player_id")
    )
    return [
        fines.record_late_fine(entry.player, game_day, entry.late_minutes)
        for entry in entries
        if not fines.has_late_fine(entry.player, game_day)
    ]


@transaction.atomic
def mark_all_present(game_day_id: int, actor: Player) -> int:
    """Mark as present everyone on the list who has no mark yet, leaving only the no-shows to mark
    by hand. Those already marked, present or absent, are kept. Returns how many were marked."""
    game_day = _lock_for_attendance(game_day_id, actor)
    return ListEntry.objects.filter(
        game_day=game_day, status=EntryStatus.LISTA.value, attendance=""
    ).update(
        attendance=Attendance.PRESENTE.value,
        attendance_marked_by=actor,
        attendance_marked_at=clock.now(),
    )
