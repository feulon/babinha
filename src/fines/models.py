from django.conf import settings
from django.db import models

from core import clock
from core.models import money_field


class FineReason(models.TextChoices):
    LATE_DROPOUT = "desistencia", "Desistência depois do prazo"
    NO_SHOW = "falta", "Falta sem aviso"
    LATE = "atraso", "Atraso no jogo"


class FineStatus(models.TextChoices):
    PENDING = "pendente", "Pendente"
    FORGIVEN = "perdoada", "Perdoada"
    VOIDED = "cancelada", "Cancelada"
    PAID = "paga", "Paga"  # reached when the diretoria approves the Pix receipt


# Occurrences that count towards the escalation. Paying does not reset the count;
# only forgiven and voided fines leave it.
COUNTED_STATUSES = (FineStatus.PENDING, FineStatus.PAID)


class Fine(models.Model):
    """One occurrence of a fineable event. Its value is fixed when it is recorded."""

    player = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="fines"
    )
    game_day = models.ForeignKey("games.GameDay", on_delete=models.PROTECT, related_name="fines")
    reason = models.CharField("motivo", max_length=12, choices=FineReason.choices)
    ordinal = models.PositiveSmallIntegerField("ocorrência")
    amount = money_field("valor")
    status = models.CharField(max_length=10, choices=FineStatus.choices, default=FineStatus.PENDING)
    created_at = models.DateTimeField("registrada em", default=clock.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    resolution_note = models.CharField("motivo", max_length=200, blank=True)

    class Meta:
        verbose_name = "multa"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["player", "game_day"],
                condition=models.Q(reason="falta", status="pendente"),
                name="one_pending_no_show_per_sunday",
            ),
            models.UniqueConstraint(
                fields=["player", "game_day"],
                condition=models.Q(reason="atraso", status__in=["pendente", "paga"]),
                name="one_active_late_fine_per_sunday",
            ),
            models.CheckConstraint(condition=models.Q(ordinal__gte=1), name="fine_ordinal_min"),
        ]

    def __str__(self) -> str:
        return f"{self.get_reason_display()} - {self.player} ({self.game_day})"

    @property
    def escalates(self) -> bool:
        """Whether it counts towards the 1st/2nd/3rd occurrence (late fines have fixed values)."""
        return self.reason != FineReason.LATE

    @property
    def is_pending(self) -> bool:
        return self.status == FineStatus.PENDING
