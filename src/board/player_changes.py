"""What editing a player does not change by itself: the Sunday's list and teams keep the position,
type and pote the player had when they were made. The diretoria is told when it matters."""

from dataclasses import dataclass

from accounts.models import Player
from core import clock
from games.domain.schedule import upcoming_sunday
from games.domain.types import EntryStatus
from games.models import ListEntry
from matchday.models import LineupSpot

WATCHED = ("player_type", "is_mensalista", "position", "pote", "is_active")


@dataclass(frozen=True)
class Snapshot:
    player_type: str
    is_mensalista: bool
    position: str
    pote: int
    is_active: bool

    @classmethod
    def of(cls, player: Player) -> "Snapshot":
        return cls(*(getattr(player, name) for name in WATCHED))


def sunday_warnings(before: Snapshot, player: Player) -> list[str]:
    """Warnings for a saved edit, about the list and the teams of the upcoming Sunday."""
    after = Snapshot.of(player)
    if after == before:
        return []
    sunday = upcoming_sunday(clock.now())
    entry = ListEntry.objects.filter(
        game_day__date=sunday,
        player=player,
        status__in=[EntryStatus.LISTA.value, EntryStatus.ESPERA.value],
    ).first()
    if entry is None:
        return []
    day = f"{sunday:%d/%m}"
    where = "na espera" if entry.status == EntryStatus.ESPERA.value else "na lista"
    warnings = []
    if after.position != before.position:
        position = entry.get_position_display().lower()
        warnings.append(
            f"{player.display_name} está {where} do domingo {day} como {position}. "
            "Mudar a posição não o move na lista nem nos times: retire e coloque de novo."
        )
    if (after.player_type, after.is_mensalista) != (before.player_type, before.is_mensalista):
        warnings.append(
            f"{player.display_name} está {where} do domingo {day}. A cobrança do domingo só é "
            "refeita com o tipo novo quando ele sair e entrar de novo."
        )
    if (
        after.pote != before.pote
        and LineupSpot.objects.filter(lineup__game_day__date=sunday, player=player).exists()
    ):
        warnings.append(
            f"{player.display_name} já está nos times do domingo {day}. O pote novo só vale em um "
            "novo sorteio; os times ficam marcados para ajuste."
        )
    if not after.is_active and before.is_active:
        warnings.append(
            f"{player.display_name} continua {where} do domingo {day}. "
            "Retire-o da lista se não vem."
        )
    return warnings
