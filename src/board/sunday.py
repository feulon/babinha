"""What the Sunday screen's four steps need: the teams, the games, the attendance and the end of
the game, in the order the diretoria works through them (teams, then games, then attendance
during the games, then the no-show and late fines and the charges)."""

from dataclasses import dataclass
from decimal import Decimal

from core.models import ClubSettings
from fines.models import Fine, FineReason, FineStatus
from games.models import Attendance, GameDay, ListEntry
from games.selectors import BoardRoster
from matchday.models import Match
from matchday.selectors import Teams, lineup_state, teams_of

TIMES, JOGOS, PRESENCA = "times", "jogos", "presenca"


@dataclass(frozen=True)
class AttendanceGroup:
    key: str
    title: str
    rows: tuple[ListEntry, ...]

    @property
    def marked(self) -> int:
        return sum(1 for row in self.rows if row.attendance)


@dataclass(frozen=True)
class LateRow:
    entry: ListEntry
    amount: Decimal
    fined: bool


@dataclass(frozen=True)
class NoShowRow:
    """Someone marked as absent. The fine is recorded when the mark is made, so it needs no
    action here: this only shows what was charged. `fine` is None if none stands."""

    entry: ListEntry
    fine: Fine | None


def _fines_of(game_day: GameDay, reason: FineReason, statuses: tuple[str, ...]) -> dict[int, Fine]:
    fines = Fine.objects.filter(game_day=game_day, reason=reason, status__in=statuses)
    return {fine.player_id: fine for fine in fines}


def _late_fines(game_day: GameDay) -> dict[int, Fine]:
    return _fines_of(game_day, FineReason.LATE, (FineStatus.PENDING, FineStatus.PAID))


def attendance_groups(roster: BoardRoster, state: Teams) -> tuple[AttendanceGroup, ...]:
    """Line players by team once there are teams, then the goalkeepers. Without teams, just
    the line."""
    groups: list[AttendanceGroup] = []
    line = list(roster.line)
    if state.lineup is not None:
        placed: set[int] = set()
        for index, team in enumerate(state.teams):
            ids = {player.pk for player in team.players}
            rows = tuple(row for row in line if row.player_id in ids)
            placed |= ids
            groups.append(AttendanceGroup(f"time-{index + 1}", team.team.name, rows))
        rest = tuple(row for row in line if row.player_id not in placed)
        if rest:
            groups.append(AttendanceGroup("sem-time", "Sem time", rest))
    else:
        groups.append(AttendanceGroup("linha", "Linha", tuple(line)))
    groups.append(AttendanceGroup("goleiros", "Goleiros", tuple(roster.goalkeepers)))
    return tuple(groups)


def late_rows(roster: BoardRoster, game_day: GameDay) -> tuple[LateRow, ...]:
    """Everyone marked late, with the fixed fine their lateness costs and whether it was applied."""
    fined = _late_fines(game_day)
    amounts = ClubSettings.load().late_fine_amounts
    return tuple(
        LateRow(row, amounts[row.late_minutes], row.player_id in fined)
        for row in (*roster.line, *roster.goalkeepers)
        if row.late_minutes
    )


def no_show_rows(roster: BoardRoster, game_day: GameDay) -> tuple[NoShowRow, ...]:
    """Everyone marked absent, with the no-show fine recorded for them. A forgiven fine still
    shows, so the row does not vanish after the diretoria forgives it."""
    fines = _fines_of(
        game_day,
        FineReason.NO_SHOW,
        (FineStatus.PENDING, FineStatus.PAID, FineStatus.FORGIVEN),
    )
    return tuple(
        NoShowRow(row, fines.get(row.player_id))
        for row in (*roster.line, *roster.goalkeepers)
        if row.attendance == Attendance.FALTOU
    )


def current_step(state_name: str, matches: int) -> str:
    """The step the diretoria is on: the teams until confirmed, then the games until there is a
    table, then the attendance."""
    if state_name != "confirmed":
        return TIMES
    return JOGOS if not matches else PRESENCA


def steps_context(game_day: GameDay, roster: BoardRoster, can_mark_attendance: bool) -> dict:
    state = teams_of(game_day)
    name = lineup_state(state)
    matches = Match.objects.filter(lineup=state.lineup).count() if state.lineup is not None else 0
    groups = attendance_groups(roster, state)
    entries = [row for group in groups for row in group.rows]
    late = late_rows(roster, game_day)
    pending = [row for row in late if not row.fined]
    return {
        "list_total": len(roster.line) + len(roster.goalkeepers),
        "teams_state": state,
        "lineup_state": name,
        "match_count": matches,
        "current_step": current_step(name, matches),
        "attendance_groups": groups,
        "attendance_total": len(entries),
        "attendance_marked": sum(1 for row in entries if row.attendance),
        "no_show_rows": no_show_rows(roster, game_day),
        "no_show_reason": FineReason.NO_SHOW.label,
        "late_rows": late,
        "fined_late_ids": {row.entry.player_id for row in late if row.fined},
        "late_pending": pending,
        "late_pending_total": sum((row.amount for row in pending), Decimal(0)),
        "can_mark_attendance": can_mark_attendance,
    }
