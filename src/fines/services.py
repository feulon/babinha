"""Recording and resolving fines.

Escalation: a player's n-th counted occurrence costs the configured base value times the
escalation factor once for each earlier occurrence.
Late fines are outside it: they cost a fixed value by lateness tier and never count.
The count never resets; only forgiving (or voiding) a fine removes it from the count. A fine's
ordinal and value are fixed when it is recorded, so later forgiveness or value changes do not
rewrite fines that already exist.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Count

from accounts.models import Player
from core import clock
from core.models import ClubSettings
from finance import charges
from fines.models import COUNTED_STATUSES, Fine, FineReason, FineStatus
from games.models import Attendance, GameDay, ListEntry

ATTENDANCE_CORRECTED = "Presença corrigida pela diretoria"
LATE_CORRECTED = "Atraso corrigido pela diretoria"


class FineError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _counted(fines):
    """The fines that count towards the escalation: late fines have fixed values and stay out."""
    return fines.filter(status__in=COUNTED_STATUSES).exclude(reason=FineReason.LATE)


def counted_occurrences(player: Player) -> int:
    return _counted(Fine.objects.filter(player=player)).count()


def next_fine(player: Player) -> tuple[int, Decimal]:
    """The ordinal and value the player's next fine would get if recorded now."""
    ordinal = counted_occurrences(player) + 1
    return ordinal, ClubSettings.load().fine_amount_for(ordinal)


@dataclass(frozen=True)
class NextFine:
    """What the player's next fine would be: which occurrence it is and its value."""

    ordinal: int
    amount: Decimal


def next_fines(players: Sequence[Player]) -> dict[int, NextFine]:
    """The next fine of each player (by id), as `record_fine` would charge it right now."""
    counted = dict(
        _counted(Fine.objects.filter(player__in=players))
        .values_list("player_id")
        .annotate(total=Count("pk"))
    )
    club = ClubSettings.load()
    ordinals = {player.pk: counted.get(player.pk, 0) + 1 for player in players}
    return {pk: NextFine(n, club.fine_amount_for(n)) for pk, n in ordinals.items()}


@transaction.atomic
def record_fine(player: Player, game_day: GameDay, reason: FineReason) -> Fine:
    # Lock the player so two fines recorded at once cannot get the same ordinal.
    Player.objects.select_for_update().only("pk").get(pk=player.pk)
    ordinal = counted_occurrences(player) + 1
    fine = Fine.objects.create(
        player=player,
        game_day=game_day,
        reason=reason,
        ordinal=ordinal,
        amount=ClubSettings.load().fine_amount_for(ordinal),
        created_at=clock.now(),
    )
    charges.open_fine_charge(fine)
    return fine


@transaction.atomic
def record_late_fine(player: Player, game_day: GameDay, minutes: int) -> Fine:
    """The fixed fine for arriving `minutes` late. It never counts in the escalation."""
    try:
        amount = ClubSettings.load().late_fine_amounts[minutes]
    except KeyError as exc:
        raise FineError("Atraso inválido.") from exc
    fine = Fine.objects.create(
        player=player,
        game_day=game_day,
        reason=FineReason.LATE,
        ordinal=1,
        amount=amount,
        created_at=clock.now(),
    )
    charges.open_fine_charge(fine)
    return fine


def _resolve(fine: Fine, status: FineStatus, actor: Player | None, note: str) -> Fine:
    fine.status = status
    fine.resolved_at = clock.now()
    fine.resolved_by = actor
    fine.resolution_note = note
    fine.save(update_fields=["status", "resolved_at", "resolved_by", "resolution_note"])
    return fine


@transaction.atomic
def forgive(fine_id: int, actor: Player, note: str) -> Fine:
    note = note.strip()
    if not note:
        raise FineError("Informe o motivo do perdão.")
    fine = Fine.objects.select_for_update().get(pk=fine_id)
    if not fine.is_pending:
        raise FineError("Só multas pendentes podem ser perdoadas.")
    if charges.fine_receipt_in_review(fine):
        raise FineError("Há um comprovante em análise para esta multa. Analise-o antes.")
    charges.cancel_fine_charge(fine, actor, "Multa perdoada")
    return _resolve(fine, FineStatus.FORGIVEN, actor, note)


def mark_paid(fine: Fine, actor: Player) -> Fine:
    """The diretoria approved the receipt. The fine keeps counting towards the escalation."""
    return _resolve(fine, FineStatus.PAID, actor, "")


def reopen_paid(fine: Fine) -> Fine:
    """The diretoria took back a confirmation: the fine is pending again."""
    return _resolve(fine, FineStatus.PENDING, None, "")


def void_pending(fines: "list[Fine]", actor: Player | None, note: str) -> int:
    """Cancel fines that no longer apply (Sunday cancelled, attendance corrected).

    A fine whose receipt is under review is left for the diretoria to settle: the money may
    already have been sent. Returns how many fines were voided.
    """
    voided = 0
    for fine in fines:
        if charges.fine_receipt_in_review(fine):
            continue
        charges.cancel_fine_charge(fine, actor, note)
        _resolve(fine, FineStatus.VOIDED, actor, note)
        voided += 1
    return voided


def void_for_game_day(game_day: GameDay, actor: Player) -> int:
    fines = list(
        Fine.objects.select_for_update().filter(game_day=game_day, status=FineStatus.PENDING)
    )
    return void_pending(fines, actor, charges.SUNDAY_CANCELLED)


def void_no_show(player: Player, game_day: GameDay, actor: Player) -> int:
    fines = list(
        Fine.objects.select_for_update().filter(
            player=player, game_day=game_day, reason=FineReason.NO_SHOW, status=FineStatus.PENDING
        )
    )
    return void_pending(fines, actor, ATTENDANCE_CORRECTED)


def void_late(player: Player, game_day: GameDay, actor: Player) -> int:
    fines = list(
        Fine.objects.select_for_update().filter(
            player=player, game_day=game_day, reason=FineReason.LATE, status=FineStatus.PENDING
        )
    )
    return void_pending(fines, actor, LATE_CORRECTED)


def void_if_no_longer_due(fine: Fine, actor: Player) -> int:
    """Settle a fine whose automatic void was skipped while its receipt was under review."""
    if not fine.is_pending:
        return 0
    if fine.game_day.is_cancelled:
        return void_pending([fine], actor, charges.SUNDAY_CANCELLED)
    if (
        fine.reason == FineReason.NO_SHOW
        and not ListEntry.objects.filter(
            game_day=fine.game_day, player=fine.player, attendance=Attendance.FALTOU
        ).exists()
    ):
        return void_pending([fine], actor, ATTENDANCE_CORRECTED)
    if fine.reason == FineReason.LATE:
        entry = ListEntry.objects.filter(game_day=fine.game_day, player=fine.player).first()
        if entry is None or entry.late_minutes is None:
            return void_pending([fine], actor, LATE_CORRECTED)
    return 0


def has_late_fine(player: Player, game_day: GameDay) -> bool:
    """A late fine already recorded for this Sunday (pending or paid)."""
    return Fine.objects.filter(
        player=player, game_day=game_day, reason=FineReason.LATE, status__in=COUNTED_STATUSES
    ).exists()


def has_paid_late_fine(player: Player, game_day: GameDay) -> bool:
    return Fine.objects.filter(
        player=player, game_day=game_day, reason=FineReason.LATE, status=FineStatus.PAID
    ).exists()


def has_no_show(player: Player, game_day: GameDay) -> bool:
    """A no-show already counted for this Sunday (pending or paid)."""
    return Fine.objects.filter(
        player=player, game_day=game_day, reason=FineReason.NO_SHOW, status__in=COUNTED_STATUSES
    ).exists()
