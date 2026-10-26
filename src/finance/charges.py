"""Charge lifecycle: what each player owes, created and cancelled as the list changes.

- Sunday: a confirmed avulso or agregado owes the single fee, but only once the diretoria
  finishes the game (the clock never does). Until then nobody owes anything for the Sunday.
  Once it is over, leaving the list (or losing the spot) cancels a charge nobody paid yet. A
  cancelled Sunday charges nobody and refunds nothing automatically.
- Monthly fee: one charge per player who joined the monthly plan (accounts.MonthlyEnrollment)
  for a month, due on the configured day of that month, the same for any number of Sundays.
  Nobody renews by themselves; the Sunday fee of a player not enrolled in the month of that
  Sunday is owed like any avulso's.
- Fine: every recorded fine with a value becomes a charge, due the Saturday after.

A charge with a receipt under review or already paid is never cancelled automatically: the
money may have been sent, so the diretoria decides. Other apps call into this module from
inside their own transactions and locks; it never calls back into them.
"""

from collections.abc import Collection
from datetime import date, timedelta

from django.conf import settings

from accounts.models import MonthlyEnrollment, Player, plan_month
from core import clock
from core.models import ZERO, ClubSettings
from finance.models import Charge, ChargeKind, ChargeStatus
from fines.models import Fine
from games.domain.schedule import WeekSchedule
from games.domain.types import EntryStatus
from games.models import GameDay, ListEntry
from games.promotion import promote_agregados

SATURDAY = 5  # date.weekday()
LEFT_THE_LIST = "Saiu da lista"
SUNDAY_CANCELLED = "Domingo cancelado"


def month_start(day: date) -> date:
    return day.replace(day=1)


def next_month(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1)


def previous_month(first: date) -> date:
    return (first - timedelta(days=1)).replace(day=1)


def saturday_before(sunday: date) -> date:
    return sunday - timedelta(days=1)


def next_saturday_after(day: date) -> date:
    days = (SATURDAY - day.weekday()) % 7 or 7
    return day + timedelta(days=days)


def monthly_due_date(month: date, due_day: int) -> date:
    return month.replace(day=due_day)


def cancel(charge: Charge, actor: Player | None, reason: str, *, waived: bool = False) -> Charge:
    charge.status = ChargeStatus.WAIVED if waived else ChargeStatus.CANCELLED
    charge.cancelled_at = clock.now()
    charge.cancelled_by = actor
    charge.cancel_reason = reason
    charge.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason"])
    return charge


def sync_game_charges(
    game_day: GameDay,
    player_ids: Collection[int] | None = None,
    *,
    actor: Player | None = None,
) -> None:
    """Make the Sunday charges of `player_ids` (everyone when None) match the list.

    Called while the caller holds the Sunday's row lock, right after the list changed. The
    mensalistas of the Sunday's month pay through the monthly fee instead.
    Before the game is over (and the Sunday is not cancelled) there is nothing to sync: the
    fees are opened by `finish_game_day`.
    """
    if player_ids is not None and not player_ids:
        return
    if not game_day.is_finished and not game_day.is_cancelled:
        return
    confirmed = ListEntry.objects.filter(game_day=game_day, status=EntryStatus.LISTA)
    charges = Charge.objects.select_for_update().filter(game_day=game_day, kind=ChargeKind.GAME)
    if player_ids is not None:
        confirmed = confirmed.filter(player_id__in=player_ids)
        charges = charges.filter(player_id__in=player_ids)

    list(
        Player.objects.select_for_update(no_key=True)
        .filter(pk__in=confirmed.values("player_id"))
        .order_by("pk")
    )
    owing = set()
    if not game_day.is_cancelled:
        opens = WeekSchedule(game_day.date, settings.BABA_LIST_OPENS_AT).charges_open
        cutoff = min(game_day.finished_at, opens)
        mensalistas = MonthlyEnrollment.objects.filter(
            month=month_start(game_day.date), enrolled_at__lte=cutoff
        ).exclude(player_id__in=charges.values("player_id"))
        owing = set(
            confirmed.exclude(player__in=mensalistas.values("player_id")).values_list(
                "player_id", flat=True
            )
        )
    existing = {c.player_id: c for c in charges.exclude(status=ChargeStatus.CANCELLED)}

    reason = SUNDAY_CANCELLED if game_day.is_cancelled else LEFT_THE_LIST
    for player_id, charge in existing.items():
        if player_id not in owing and charge.is_payable:
            cancel(charge, actor, reason)

    fee = ClubSettings.load().single_fee
    missing = owing - existing.keys()
    if fee <= ZERO or not missing:
        return
    today = clock.now().date()
    Charge.objects.bulk_create(
        Charge(
            player_id=player_id,
            kind=ChargeKind.GAME,
            amount=fee,
            reference_date=game_day.date,
            due_date=max(saturday_before(game_day.date), today),
            game_day=game_day,
            created_at=clock.now(),
        )
        for player_id in sorted(missing)
    )


def finish_game_day(game_day: GameDay, actor: Player | None = None) -> bool:
    """Mark the game over and charge everyone on the list. Call under the Sunday's row lock.

    Returns False when it was already finished, so charges are never created twice.
    """
    if game_day.is_finished or game_day.is_cancelled:
        return False
    game_day.finished_at = clock.now()
    game_day.finished_by = actor
    game_day.save(update_fields=["finished_at", "finished_by"])
    sync_game_charges(game_day, actor=actor)
    promote_agregados()
    return True


def open_fine_charge(fine: Fine) -> Charge | None:
    """Make a fine payable through the Pix flow. Fines without a value have nothing to pay."""
    if fine.amount <= ZERO:
        return None
    return Charge.objects.create(
        player_id=fine.player_id,
        kind=ChargeKind.FINE,
        amount=fine.amount,
        reference_date=fine.game_day.date,
        due_date=next_saturday_after(fine.created_at.astimezone(clock.SAO_PAULO).date()),
        game_day=fine.game_day,
        fine=fine,
        created_at=clock.now(),
    )


def fine_receipt_in_review(fine: Fine) -> bool:
    return Charge.objects.filter(fine=fine, status=ChargeStatus.UNDER_REVIEW).exists()


def cancel_fine_charge(fine: Fine, actor: Player | None, reason: str) -> None:
    """The fine was forgiven or voided, so its unpaid charge goes too."""
    charge = Charge.objects.select_for_update().filter(fine=fine).first()
    if charge is not None and charge.is_payable:
        cancel(charge, actor, reason)


def ensure_monthly_charges(month: date | None = None, player: Player | None = None) -> int:
    """Create the fee of `month` (default: the plan month, see accounts.models.plan_month) for
    its active mensalistas (or just `player`) who have none yet.

    Mensalistas are the players enrolled in the month, so it is empty for a month nobody joined.

    Safe to call on every page view: existing charges, including waived ones, are kept, and
    concurrent calls cannot duplicate a month thanks to the unique constraint.
    """
    values = ClubSettings.load()
    if values.monthly_fee <= ZERO:
        return 0
    month = month_start(month or plan_month())
    mensalistas = Player.objects.filter(is_active=True, enrollments__month=month)
    if player is not None:
        mensalistas = mensalistas.filter(pk=player.pk)
    charged = Charge.objects.filter(kind=ChargeKind.MONTHLY, reference_date=month).exclude(
        status=ChargeStatus.CANCELLED
    )
    missing = mensalistas.exclude(pk__in=charged.values("player_id")).values_list("pk", flat=True)
    created = Charge.objects.bulk_create(
        (
            Charge(
                player_id=player_id,
                kind=ChargeKind.MONTHLY,
                amount=values.monthly_fee,
                reference_date=month,
                due_date=monthly_due_date(month, values.monthly_due_day),
                created_at=clock.now(),
            )
            for player_id in missing
        ),
        ignore_conflicts=True,
    )
    return len(created)
