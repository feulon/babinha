"""E-mail notices about pending payments, sent by the diretoria to players who owe."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from accounts.models import Player
from core import clock
from core.models import ZERO
from core.templatetags.money import brl
from finance.models import PaymentReminder, ReminderOutcome
from finance.selectors import ChargeRow, pix_info

logger = logging.getLogger(__name__)

# A player reminded within this window is not reminded again unless the diretoria insists.
RECENT_WINDOW = timedelta(days=3)
SUBJECT = "Babinha de Domingo: pagamento pendente"


@dataclass(frozen=True)
class Debtor:
    player: Player
    rows: tuple[ChargeRow, ...]
    last_reminded: datetime | None

    @property
    def total(self) -> Decimal:
        return sum((row.charge.amount for row in self.rows), ZERO)

    @property
    def has_email(self) -> bool:
        return bool(self.player.email)

    @property
    def recently_reminded(self) -> bool:
        return self.last_reminded is not None


@dataclass(frozen=True)
class SendResult:
    sent: tuple[Player, ...]
    no_email: tuple[Player, ...]
    failed: tuple[Player, ...]
    skipped_recent: tuple[Player, ...]


def debtors(rows: Iterable[ChargeRow]) -> tuple[Debtor, ...]:
    """Players with something still waiting for their payment, biggest debt first."""
    by_player: dict[int, list[ChargeRow]] = {}
    for row in rows:
        if row.charge.is_payable:
            by_player.setdefault(row.charge.player_id, []).append(row)
    if not by_player:
        return ()
    since = clock.now() - RECENT_WINDOW
    last = {
        reminder.player_id: reminder.sent_at
        for reminder in PaymentReminder.objects.filter(
            player_id__in=by_player, outcome=ReminderOutcome.SENT, sent_at__gte=since
        ).order_by("sent_at")
    }
    result = [
        Debtor(group[0].charge.player, tuple(group), last.get(player_id))
        for player_id, group in by_player.items()
    ]
    return tuple(sorted(result, key=lambda d: (-d.total, d.player.name.lower())))


def compose(debtor: Debtor, app_url: str) -> str:
    pix = pix_info()
    return render_to_string(
        "board/email/payment_reminder.txt",
        {
            "player": debtor.player,
            "rows": debtor.rows,
            "total": brl(debtor.total),
            "pix_key": pix.key,
            "pix_holder": pix.holder,
            "app_url": app_url,
        },
    )


def send_reminders(
    selected: Iterable[Debtor], sent_by: Player, app_url: str, *, repeat: bool
) -> SendResult:
    sent: list[Player] = []
    no_email: list[Player] = []
    failed: list[Player] = []
    skipped: list[Player] = []
    for debtor in selected:
        player = debtor.player
        if debtor.recently_reminded and not repeat:
            skipped.append(player)
            continue
        outcome = _deliver(debtor, app_url)
        PaymentReminder.objects.create(
            player=player, sent_by=sent_by, amount=debtor.total, outcome=outcome
        )
        {
            ReminderOutcome.SENT: sent,
            ReminderOutcome.NO_EMAIL: no_email,
            ReminderOutcome.FAILED: failed,
        }[outcome].append(player)
    return SendResult(tuple(sent), tuple(no_email), tuple(failed), tuple(skipped))


def _deliver(debtor: Debtor, app_url: str) -> str:
    if not debtor.has_email:
        return ReminderOutcome.NO_EMAIL
    message = EmailMessage(SUBJECT, compose(debtor, app_url), to=[debtor.player.email])
    try:
        message.send()
    except Exception:
        logger.exception("Payment reminder to player %s failed", debtor.player.pk)
        return ReminderOutcome.FAILED
    return ReminderOutcome.SENT
