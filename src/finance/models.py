from datetime import date

from django.conf import settings
from django.core.files.storage import Storage, storages
from django.db import models

from core import clock
from core.models import ZERO, money_field


def receipts_storage() -> Storage:
    """Private S3-compatible bucket (see STORAGES["receipts"]). Resolved lazily so tests and
    deploys can swap the backend without touching migrations."""
    return storages["receipts"]


class ChargeKind(models.TextChoices):
    MONTHLY = "mensalidade", "Mensalidade"
    GAME = "jogo", "Domingo"
    FINE = "multa", "Multa"


class ChargeStatus(models.TextChoices):
    PENDING = "pendente", "Pendente"
    UNDER_REVIEW = "em_analise", "Em análise"
    REFUSED = "recusada", "Recusada"
    PAID = "paga", "Paga"
    # No longer owed: the player left the list, the Sunday was cancelled or the fine was
    # forgiven or voided. Never counts as existing, so rejoining the list charges again.
    CANCELLED = "cancelada", "Cancelada"
    # Dispensed by hand by the diretoria. Counts as existing, so it is never charged again.
    WAIVED = "dispensada", "Dispensada"


class PaymentMethod(models.TextChoices):
    """How money reached the diretoria when no Pix receipt was sent."""

    CASH = "dinheiro", "Dinheiro"
    PIX = "pix", "Pix sem comprovante"
    OTHER = "outro", "Outra forma"


# Still owed and waiting for a (new) receipt from the player.
PAYABLE_STATUSES = (ChargeStatus.PENDING, ChargeStatus.REFUSED)
# Not settled yet: shows up as pending for the diretoria.
OPEN_STATUSES = (*PAYABLE_STATUSES, ChargeStatus.UNDER_REVIEW)


class Charge(models.Model):
    """Something a player owes: the monthly fee, one Sunday (non-mensalistas) or a fine.

    The amount is fixed when the charge is created, so later value changes never rewrite it.
    `reference_date` is the period it belongs to: the first day of the month for the monthly
    fee and the Sunday for game and fine charges. The finance report groups by it.
    """

    player = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="charges"
    )
    kind = models.CharField("tipo", max_length=12, choices=ChargeKind.choices)
    status = models.CharField(
        max_length=12, choices=ChargeStatus.choices, default=ChargeStatus.PENDING
    )
    amount = money_field("valor")
    reference_date = models.DateField("referente a")
    due_date = models.DateField("vencimento")
    game_day = models.ForeignKey(
        "games.GameDay",
        verbose_name="domingo",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="charges",
    )
    fine = models.OneToOneField(
        "fines.Fine", on_delete=models.PROTECT, null=True, blank=True, related_name="charge"
    )
    created_at = models.DateTimeField("criada em", default=clock.now)
    paid_at = models.DateTimeField("paga em", null=True, blank=True)
    cancelled_at = models.DateTimeField("cancelada ou dispensada em", null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="cancelada ou dispensada por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    cancel_reason = models.CharField("motivo", max_length=200, blank=True)
    # Set only when the diretoria marked the charge as paid by hand (no Pix receipt).
    payment_method = models.CharField(
        "forma de pagamento", max_length=10, choices=PaymentMethod.choices, blank=True
    )
    payment_note = models.CharField("nota do pagamento", max_length=200, blank=True)
    marked_paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="marcada como paga por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "cobrança"
        verbose_name_plural = "cobranças"
        ordering = ["due_date", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=ZERO), name="charge_amount_gt_0"),
            models.CheckConstraint(
                condition=(
                    models.Q(kind="mensalidade", game_day=None, fine=None, reference_date__day=1)
                    | models.Q(kind="jogo", game_day__isnull=False, fine=None)
                    | models.Q(kind="multa", game_day__isnull=False, fine__isnull=False)
                ),
                name="charge_kind_references",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="paga", paid_at__isnull=False)
                    | (~models.Q(status="paga") & models.Q(paid_at=None))
                ),
                name="paid_charges_have_paid_at",
            ),
            models.UniqueConstraint(
                fields=["player", "game_day"],
                condition=models.Q(kind="jogo") & ~models.Q(status="cancelada"),
                name="one_game_charge_per_sunday",
            ),
            models.UniqueConstraint(
                fields=["player", "reference_date"],
                condition=models.Q(kind="mensalidade") & ~models.Q(status="cancelada"),
                name="one_monthly_charge_per_month",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "due_date"]),
            models.Index(fields=["reference_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.description} - {self.player}"

    @property
    def description(self) -> str:
        if self.kind == ChargeKind.MONTHLY:
            return f"Mensalidade {self.reference_date:%m/%Y}"
        if self.kind == ChargeKind.GAME:
            return f"Domingo {self.reference_date:%d/%m}"
        return f"Multa · domingo {self.reference_date:%d/%m}"

    @property
    def is_payable(self) -> bool:
        return self.status in PAYABLE_STATUSES

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def is_overdue(self, today: date) -> bool:
        return self.is_open and today > self.due_date


class ReceiptStatus(models.TextChoices):
    UNDER_REVIEW = "em_analise", "Em análise"
    APPROVED = "aprovado", "Aprovado"
    REFUSED = "recusado", "Recusado"
    # The player sent another receipt while this one was still waiting for the diretoria.
    SUPERSEDED = "substituido", "Substituído"


class Receipt(models.Model):
    """A Pix receipt (photo or PDF) the player sent for a charge. Kept forever, for audits: a
    refused or replaced receipt stays (row and file), and the player sends a new one. Only
    erasing the player (manage.py apagar_jogador, a LGPD request) deletes the row and the file."""

    charge = models.ForeignKey(Charge, on_delete=models.PROTECT, related_name="receipts")
    file = models.FileField("arquivo", storage=receipts_storage, max_length=200)
    content_type = models.CharField(max_length=40)
    size = models.PositiveIntegerField("tamanho em bytes")
    original_name = models.CharField("nome original", max_length=120, blank=True)
    status = models.CharField(
        max_length=12, choices=ReceiptStatus.choices, default=ReceiptStatus.UNDER_REVIEW
    )
    submitted_at = models.DateTimeField("enviado em", default=clock.now)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    reviewed_at = models.DateTimeField("analisado em", null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="analisado por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    refusal_reason = models.CharField("motivo da recusa", max_length=200, blank=True)

    class Meta:
        verbose_name = "comprovante"
        ordering = ["-submitted_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["charge"],
                condition=models.Q(status="em_analise"),
                name="one_receipt_under_review_per_charge",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="recusado") | ~models.Q(refusal_reason=""),
                name="refused_receipts_have_reason",
            ),
        ]

    def __str__(self) -> str:
        return f"Comprovante de {self.charge}"

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")


class Expense(models.Model):
    """Money the diretoria spent (field rent, balls, ...), entered by hand."""

    amount = money_field("valor")
    description = models.CharField("descrição", max_length=200)
    date = models.DateField("data")
    created_at = models.DateTimeField("lançada em", default=clock.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    updated_at = models.DateTimeField("alterada em", auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        verbose_name = "despesa"
        ordering = ["-date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=ZERO), name="expense_amount_gt_0"),
        ]
        indexes = [models.Index(fields=["date"])]

    def __str__(self) -> str:
        return f"{self.description} ({self.date:%d/%m/%Y})"


class ReminderOutcome(models.TextChoices):
    SENT = "enviado", "Enviado"
    NO_EMAIL = "sem_email", "Sem e-mail"
    FAILED = "falhou", "Falhou"


class PaymentReminder(models.Model):
    """One e-mail notice about pending payments the diretoria sent (or tried to) to a player.

    The log that tells the diretoria a player was already reminded, so a second click does not
    send the same notice again by accident."""

    player = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_reminders"
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    sent_at = models.DateTimeField("enviado em", default=clock.now)
    amount = money_field("valor devido")
    outcome = models.CharField(max_length=10, choices=ReminderOutcome.choices)

    class Meta:
        verbose_name = "aviso de pagamento"
        verbose_name_plural = "avisos de pagamento"
        ordering = ["-sent_at", "-pk"]
        indexes = [models.Index(fields=["player", "sent_at"])]

    def __str__(self) -> str:
        return f"Aviso para {self.player} em {self.sent_at:%d/%m/%Y}"
