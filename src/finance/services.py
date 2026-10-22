"""Paying through Pix receipts, the diretoria's review, waivers and expenses.

Lock order, shared with the list operations: Sunday (GameDay), player (for Sunday charges),
charge, then receipt. Approving never needs the Sunday; refusing a Sunday or fine charge takes
it first because it may cancel the charge when the player is no longer on the list or the fine
no longer applies.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from accounts.models import Player
from core import clock
from finance import charges, receipt_files
from finance.models import (
    Charge,
    ChargeKind,
    ChargeStatus,
    Expense,
    PaymentMethod,
    Receipt,
    ReceiptStatus,
)
from fines import services as fines
from games.models import GameDay

logger = logging.getLogger(__name__)

REASON_MAX = 200
# How long a confirmed receipt can be taken back, so a wrong tap is not final.
UNDO_WINDOW = timedelta(minutes=10)


class PaymentError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _require_board(actor: Player) -> None:
    if not actor.is_board:
        raise PermissionDenied


def _required_reason(reason: str, message: str) -> str:
    reason = reason.strip()
    if not reason:
        raise PaymentError(message)
    return reason[:REASON_MAX]


_NOT_PAYABLE = {
    ChargeStatus.UNDER_REVIEW: "Já há um comprovante em análise para esta cobrança.",
    ChargeStatus.PAID: "Esta cobrança já está paga.",
    ChargeStatus.CANCELLED: "Esta cobrança foi cancelada.",
    ChargeStatus.WAIVED: "Esta cobrança foi dispensada pela diretoria.",
}


def _supersede_pending_receipt(charge: Charge) -> None:
    """The player replaces a receipt the diretoria has not looked at yet: the old one stays as
    history, no longer waiting for review."""
    Receipt.objects.filter(charge=charge, status=ReceiptStatus.UNDER_REVIEW).update(
        status=ReceiptStatus.SUPERSEDED
    )


@transaction.atomic
def submit_receipt(charge_id: int, player: Player, upload: UploadedFile) -> Receipt:
    """Store the player's Pix receipt and put the charge under review.

    While a receipt is still under review the player can send another one in its place.
    """
    kind = receipt_files.inspect(upload)
    charge = Charge.objects.select_for_update().filter(pk=charge_id).first()
    if charge is None or charge.player_id != player.pk:
        raise PermissionDenied
    replacing = charge.status == ChargeStatus.UNDER_REVIEW
    if not (charge.is_payable or replacing):
        raise PaymentError(_NOT_PAYABLE[ChargeStatus(charge.status)])

    now = clock.now()
    receipt = Receipt(
        charge=charge,
        content_type=kind.content_type,
        size=upload.size,
        original_name=receipt_files.display_name(upload),
        submitted_at=now,
        submitted_by=player,
    )
    receipt.file.save(receipt_files.storage_name(kind, now), upload, save=False)
    try:
        if replacing:
            _supersede_pending_receipt(charge)
        receipt.save()
        charge.status = ChargeStatus.UNDER_REVIEW
        charge.save(update_fields=["status"])
    except Exception:
        # Do not leave an orphan file in the bucket when the database refuses the receipt.
        receipt.file.storage.delete(receipt.file.name)
        raise
    return receipt


def _lock_for_review(receipt_id: int, *, with_sunday: bool) -> Receipt:
    receipt = Receipt.objects.select_related("charge").get(pk=receipt_id)
    if with_sunday and receipt.charge.game_day_id is not None:
        GameDay.objects.select_for_update().get(pk=receipt.charge.game_day_id)
        if receipt.charge.kind == ChargeKind.GAME:
            Player.objects.select_for_update(no_key=True).get(pk=receipt.charge.player_id)
    Charge.objects.select_for_update().get(pk=receipt.charge_id)
    receipt = Receipt.objects.select_for_update().select_related("charge").get(pk=receipt_id)
    if receipt.status == ReceiptStatus.SUPERSEDED:
        raise PaymentError("O jogador enviou outro comprovante no lugar deste.")
    if receipt.status != ReceiptStatus.UNDER_REVIEW:
        raise PaymentError("Este comprovante já foi analisado.")
    return receipt


def _mark_reviewed(receipt: Receipt, status: ReceiptStatus, actor: Player, reason: str) -> None:
    receipt.status = status
    receipt.reviewed_at = clock.now()
    receipt.reviewed_by = actor
    receipt.refusal_reason = reason
    receipt.save(update_fields=["status", "reviewed_at", "reviewed_by", "refusal_reason"])


@transaction.atomic
def approve_receipt(receipt_id: int, actor: Player) -> Receipt:
    """The money arrived: the charge (and its fine, if any) is paid."""
    _require_board(actor)
    receipt = _lock_for_review(receipt_id, with_sunday=False)
    _mark_reviewed(receipt, ReceiptStatus.APPROVED, actor, "")
    charge = receipt.charge
    charge.status = ChargeStatus.PAID
    charge.paid_at = receipt.reviewed_at
    charge.save(update_fields=["status", "paid_at"])
    if charge.fine_id is not None:
        fines.mark_paid(charge.fine, actor)
    return receipt


def can_undo_approval(receipt: Receipt) -> bool:
    return (
        receipt.status == ReceiptStatus.APPROVED
        and receipt.reviewed_at is not None
        and clock.now() - receipt.reviewed_at <= UNDO_WINDOW
    )


@transaction.atomic
def undo_approval(receipt_id: int, actor: Player) -> Receipt:
    """Take back a confirmation made by mistake: the receipt waits for review again.

    Only within UNDO_WINDOW of the confirmation, and only while the charge is still paid.
    """
    _require_board(actor)
    receipt = Receipt.objects.select_related("charge").get(pk=receipt_id)
    Charge.objects.select_for_update().get(pk=receipt.charge_id)
    receipt = Receipt.objects.select_for_update().select_related("charge").get(pk=receipt_id)
    charge = receipt.charge
    if not can_undo_approval(receipt) or charge.status != ChargeStatus.PAID:
        raise PaymentError("Só dá para desfazer uma confirmação até 10 minutos depois dela.")
    receipt.status = ReceiptStatus.UNDER_REVIEW
    receipt.reviewed_at = None
    receipt.reviewed_by = None
    receipt.save(update_fields=["status", "reviewed_at", "reviewed_by"])
    charge.status = ChargeStatus.UNDER_REVIEW
    charge.paid_at = None
    charge.save(update_fields=["status", "paid_at"])
    if charge.fine_id is not None:
        fines.reopen_paid(charge.fine)
    return receipt


@transaction.atomic
def refuse_receipt(receipt_id: int, actor: Player, reason: str) -> Receipt:
    """The receipt does not prove the payment. The player sees the reason and can send another.

    A refused Sunday charge of someone who is no longer on the list is cancelled right away, and
    so is a refused fine whose Sunday was cancelled or whose no-show was corrected meanwhile.
    """
    _require_board(actor)
    reason = _required_reason(reason, "Informe o motivo da recusa.")
    receipt = _lock_for_review(receipt_id, with_sunday=True)
    _mark_reviewed(receipt, ReceiptStatus.REFUSED, actor, reason)
    charge = receipt.charge
    charge.status = ChargeStatus.REFUSED
    charge.save(update_fields=["status"])
    if charge.kind == ChargeKind.GAME:
        charges.sync_game_charges(charge.game_day, [charge.player_id], actor=actor)
    elif charge.fine_id is not None:
        fines.void_if_no_longer_due(charge.fine, actor)
    return receipt


@transaction.atomic
def waive_charge(charge_id: int, actor: Player, reason: str) -> Charge:
    """Dispense a monthly or Sunday charge by hand. Fines are forgiven on the fines screen."""
    _require_board(actor)
    reason = _required_reason(reason, "Informe o motivo da dispensa.")
    charge = Charge.objects.select_for_update().get(pk=charge_id)
    if charge.kind == ChargeKind.FINE:
        raise PaymentError("Multas são perdoadas na tela de multas.")
    if not charge.is_payable:
        raise PaymentError(_NOT_PAYABLE[ChargeStatus(charge.status)])
    return charges.cancel(charge, actor, reason, waived=True)


@transaction.atomic
def mark_paid(charge_id: int, actor: Player, method: str, note: str = "") -> Charge:
    """The money arrived without a Pix receipt (cash on the court, a Pix nobody photographed).

    The charge becomes paid, so it enters the Caixa; who marked it, how and why stays on it.
    """
    _require_board(actor)
    if method not in PaymentMethod.values:
        raise PaymentError("Escolha a forma de pagamento.")
    charge = Charge.objects.select_for_update().get(pk=charge_id)
    if not charge.is_payable:
        raise PaymentError(_NOT_PAYABLE[ChargeStatus(charge.status)])
    charge.status = ChargeStatus.PAID
    charge.paid_at = clock.now()
    charge.payment_method = method
    charge.payment_note = note.strip()[:REASON_MAX]
    charge.marked_paid_by = actor
    charge.save(
        update_fields=["status", "paid_at", "payment_method", "payment_note", "marked_paid_by"]
    )
    if charge.fine_id is not None:
        fines.mark_paid(charge.fine, actor)
    return charge


def save_expense(
    actor: Player,
    *,
    amount: Decimal,
    description: str,
    day: date,
    expense: Expense | None = None,
) -> Expense:
    """Create an expense, or update `expense` when given."""
    _require_board(actor)
    expense = expense or Expense(created_by=actor, created_at=clock.now())
    expense.amount = amount
    expense.description = description.strip()
    expense.date = day
    expense.updated_by = actor
    expense.full_clean()
    expense.save()
    return expense


def delete_expense(expense_id: int, actor: Player) -> None:
    _require_board(actor)
    deleted, _ = Expense.objects.filter(pk=expense_id).delete()
    if deleted:
        logger.info("Expense %s deleted by player %s", expense_id, actor.pk)
