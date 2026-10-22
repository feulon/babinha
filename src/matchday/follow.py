"""The viewer's presence and the complete roster at the top of the Sunday screen."""

from dataclasses import dataclass

from accounts.models import Player
from games.models import Attendance, EntryStatusChoices, GameDay, ListEntry
from games.selectors import WeekView, build_week_view


@dataclass(frozen=True)
class Follow:
    week: WeekView  # the complete list of that Sunday
    entry: ListEntry | None  # the viewer's own sign-up

    @property
    def presence(self) -> str:
        """The viewer's own line: present (and how late), absent, on the list, waiting or out."""
        entry = self.entry
        if entry is None or entry.status == EntryStatusChoices.SAIU:
            return "Você não está na lista"
        if entry.status == EntryStatusChoices.ESPERA:
            return "Você está na espera"
        if entry.attendance == Attendance.FALTOU:
            return "Você faltou"
        if entry.attendance == Attendance.PRESENTE:
            late = f" · chegou {entry.late_minutes} min atrasado" if entry.late_minutes else ""
            return f"Você está presente{late}"
        return "Você está na lista"


def follow_of(game_day: GameDay, viewer: Player) -> Follow:
    return Follow(
        week=build_week_view(game_day, viewer),
        entry=ListEntry.objects.filter(game_day=game_day, player=viewer).first(),
    )
