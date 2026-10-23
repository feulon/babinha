"""Read models for the payment and finance screens."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Prefetch, QuerySet, Sum

from accounts.models import MonthlyEnrollment, Player, current_month
from core.models import ZERO, ClubSettings
from finance.charges import month_start, next_month
from finance.models import (
    OPEN_STATUSES,
    Charge,
    ChargeKind,
    ChargeStatus,
    Expense,
    Receipt,
    ReceiptStatus,
)
from fines.models import FineReason
from games.models import GameDay


@dataclass(frozen=True)
class PixInfo:
    key: str
    holder: str  # who the key is under, as the presidente wrote it; "" when not informed

    @property
    def is_configured(self) -> bool:
        return bool(self.key)


def pix_info() -> PixInfo:
    club = ClubSettings.load()
    return PixInfo(club.pix_key, club.pix_holder)


@dataclass(frozen=True)
class ChargeRow:
    """A charge with its latest receipt, as the player and the diretoria see it."""

    charge: Charge
    latest_receipt: Receipt | None
    overdue: bool

    @property
    def refusal_reason(self) -> str:
        receipt = self.latest_receipt
        if receipt is not None and receipt.status == ReceiptStatus.REFUSED:
            return receipt.refusal_reason
        return ""


def _with_receipts(charges: QuerySet[Charge]) -> QuerySet[Charge]:
    return charges.select_related("player", "game_day", "fine", "marked_paid_by").prefetch_related(
        Prefetch(
            "receipts",
            queryset=Receipt.objects.select_related("reviewed_by"),
            to_attr="receipt_list",
        )
    )


def _rows(charges: QuerySet[Charge], today: date) -> tuple[ChargeRow, ...]:
    return tuple(
        ChargeRow(c, c.receipt_list[0] if c.receipt_list else None, c.is_overdue(today))
        for c in _with_receipts(charges)
    )


@dataclass(frozen=True)
class PlayerPayments:
    open: tuple[ChargeRow, ...]
    history: tuple[ChargeRow, ...]

    @property
    def payable(self) -> tuple[ChargeRow, ...]:
        return tuple(row for row in self.open if row.charge.is_payable)

    @property
    def has_overdue(self) -> bool:
        return any(row.overdue for row in self.payable)

    @property
    def total_payable(self) -> Decimal:
        return sum((row.charge.amount for row in self.payable), ZERO)


HISTORY_LIMIT = 30


def player_payments(player: Player, today: date) -> PlayerPayments:
    mine = Charge.objects.filter(player=player)
    open_ = mine.filter(status__in=OPEN_STATUSES).order_by("due_date", "pk")
    history = mine.exclude(status__in=OPEN_STATUSES).order_by("-reference_date", "-pk")
    return PlayerPayments(_rows(open_, today), _rows(history[:HISTORY_LIMIT], today))


def sunday_charges(player: Player, game_day: GameDay, today: date) -> tuple[ChargeRow, ...]:
    """What a player owes for one Sunday: the game fee and any fine, except what was cancelled."""
    mine = Charge.objects.filter(player=player, game_day=game_day)
    return _rows(mine.exclude(status=ChargeStatus.CANCELLED), today)


@dataclass(frozen=True)
class HistoryEntry:
    """One charge in a player's payment history, with every receipt ever sent for it."""

    charge: Charge
    receipts: tuple[Receipt, ...]  # newest first; refused and replaced ones stay

    @property
    def reason(self) -> str:
        charge = self.charge
        if charge.kind == ChargeKind.MONTHLY:
            return "Mensalidade"
        if charge.kind == ChargeKind.GAME:
            return "Jogo"
        return _FINE_REASONS.get(charge.fine.reason, "Multa") if charge.fine else "Multa"

    @property
    def situation(self) -> str:
        return _SITUATIONS.get(self.charge.status, self.charge.get_status_display())


_FINE_REASONS = {
    FineReason.NO_SHOW: "Multa de falta",
    FineReason.LATE: "Multa de atraso",
    FineReason.LATE_DROPOUT: "Multa de desistência",
}
# What the diretoria reads for each charge status: open charges are all "pendente".
_SITUATIONS = {
    ChargeStatus.PENDING: "Pendente",
    ChargeStatus.UNDER_REVIEW: "Pendente",
    ChargeStatus.REFUSED: "Recusado",
    ChargeStatus.PAID: "Aprovado",
    ChargeStatus.WAIVED: "Dispensado",
    ChargeStatus.CANCELLED: "Cancelado",
}


def payment_history(player: Player) -> tuple[HistoryEntry, ...]:
    """Every charge of the player, newest first, each with all its receipts (audit trail)."""
    mine = Charge.objects.filter(player=player).order_by("-reference_date", "-pk")
    return tuple(
        HistoryEntry(c, tuple(c.receipt_list))
        for c in _with_receipts(mine).select_related("cancelled_by")
    )


@dataclass(frozen=True)
class MonthlyPlan:
    """Where a player stands in this month's plan, for the "Virar mensalista" card."""

    month: date
    enrolled: bool
    fee: Decimal
    charge: Charge | None  # the month's fee once they joined

    @property
    def next_month(self) -> date:
        return next_month(self.month)

    @property
    def is_offered(self) -> bool:
        """Nothing to join when the diretoria turned the monthly fee off (R$ 0,00)."""
        return self.fee > ZERO


def monthly_plan(player: Player, *, month: date | None = None) -> MonthlyPlan:
    month = month if month is not None else current_month()
    enrolled = MonthlyEnrollment.objects.filter(player=player, month=month).exists()
    charge = (
        Charge.objects.filter(player=player, kind=ChargeKind.MONTHLY, reference_date=month)
        .exclude(status=ChargeStatus.CANCELLED)
        .first()
        if enrolled
        else None
    )
    return MonthlyPlan(month, enrolled, ClubSettings.load().monthly_fee, charge)


def review_queue() -> QuerySet[Receipt]:
    return (
        Receipt.objects.filter(status=ReceiptStatus.UNDER_REVIEW)
        .select_related("charge", "charge__player", "charge__game_day")
        .order_by("submitted_at", "pk")
    )


def open_charges(today: date) -> tuple[ChargeRow, ...]:
    """Everything not settled yet, overdue first, for the diretoria's pending list."""
    rows = _rows(Charge.objects.filter(status__in=OPEN_STATUSES), today)
    return tuple(
        sorted(rows, key=lambda r: (not r.overdue, r.charge.due_date, r.charge.player.name))
    )


def late_mensalistas(today: date) -> tuple[ChargeRow, ...]:
    """Monthly fees past their due date, including debts from previous months."""
    late = Charge.objects.filter(
        kind=ChargeKind.MONTHLY, status__in=OPEN_STATUSES, due_date__lt=today
    ).order_by("due_date", "player__name")
    return _rows(late, today)


@dataclass(frozen=True)
class MemberRow:
    """A player who joined the month's plan and where their fee stands (None: no fee was opened,
    e.g. the monthly value was off when they joined)."""

    enrollment: MonthlyEnrollment
    fee: ChargeRow | None

    @property
    def is_paid(self) -> bool:
        return self.fee is not None and self.fee.charge.status == ChargeStatus.PAID

    @property
    def is_open(self) -> bool:
        return self.fee is not None and self.fee.charge.is_open


def month_members(month: date, today: date) -> tuple[MemberRow, ...]:
    """Who joined the plan in `month`, with their fee, by name."""
    enrollments = list(
        MonthlyEnrollment.objects.filter(month=month_start(month))
        .select_related("player", "enrolled_by")
        .order_by("player__name", "pk")
    )
    fees = {
        row.charge.player_id: row
        for row in _rows(
            Charge.objects.filter(kind=ChargeKind.MONTHLY, reference_date=month_start(month))
            .exclude(status=ChargeStatus.CANCELLED)
            .filter(player__in=[e.player_id for e in enrollments]),
            today,
        )
    }
    return tuple(MemberRow(e, fees.get(e.player_id)) for e in enrollments)


@dataclass(frozen=True)
class MonthReport:
    month: date
    collected: Decimal
    collected_monthly: Decimal  # mensalidades (mensalistas)
    collected_fines: Decimal
    collected_walk_in: Decimal  # domingos pagos por avulsos
    expenses_total: Decimal
    pending_total: Decimal
    expenses: tuple[Expense, ...]
    paid: tuple[ChargeRow, ...]  # filled for the diretoria only
    pending: tuple[ChargeRow, ...]  # filled for the diretoria only
    members: tuple[MemberRow, ...]  # who joined the monthly plan; the diretoria only

    @property
    def members_paid(self) -> int:
        return sum(1 for member in self.members if member.is_paid)

    @property
    def members_open(self) -> int:
        return sum(1 for member in self.members if member.is_open)

    @property
    def balance(self) -> Decimal:
        return self.collected - self.expenses_total


def month_report(month: date, today: date, *, with_people: bool) -> MonthReport:
    """Totals of the month (by the period each charge refers to, and expense date).

    Everyone sees the totals; `with_people` adds who paid and who is pending (diretoria only).
    """
    first = month_start(month)
    following = next_month(first)
    charges = Charge.objects.filter(reference_date__gte=first, reference_date__lt=following)
    paid = charges.filter(status=ChargeStatus.PAID)
    pending = charges.filter(status__in=OPEN_STATUSES)
    expenses = Expense.objects.filter(date__gte=first, date__lt=following).select_related(
        "created_by", "updated_by"
    )

    def total(queryset: QuerySet) -> Decimal:
        return queryset.aggregate(total=Sum("amount"))["total"] or ZERO

    def total_of(kind: ChargeKind) -> Decimal:
        return total(paid.filter(kind=kind))

    by_name = ("player__name", "reference_date", "pk")
    return MonthReport(
        month=first,
        collected=total(paid),
        collected_monthly=total_of(ChargeKind.MONTHLY),
        collected_fines=total_of(ChargeKind.FINE),
        collected_walk_in=total_of(ChargeKind.GAME),
        expenses_total=total(expenses),
        pending_total=total(pending),
        expenses=tuple(expenses) if with_people else (),
        paid=_rows(paid.order_by(*by_name), today) if with_people else (),
        pending=_rows(pending.order_by(*by_name), today) if with_people else (),
        members=month_members(first, today) if with_people else (),
    )
