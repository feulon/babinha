from django.conf import settings
from django.db import models

from accounts.models import Position
from core import clock
from games.domain.types import EntryStatus


class GameDay(models.Model):
    """One Sunday of baba, created on demand when its list is first looked at."""

    date = models.DateField("domingo", unique=True)
    cancelled_at = models.DateTimeField("cancelado em", null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="cancelado por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    cancel_reason = models.CharField("motivo do cancelamento", max_length=200, blank=True)
    started_at = models.DateTimeField("iniciado em", null=True, blank=True)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="iniciado por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    finished_at = models.DateTimeField("finalizado em", null=True, blank=True)
    finished_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="finalizado por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        verbose_name = "domingo"
        ordering = ["-date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(date__week_day=1),  # Django week_day: 1 = Sunday
                name="game_day_is_sunday",
            ),
        ]

    def __str__(self) -> str:
        return f"Domingo {self.date:%d/%m/%Y}"

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None

    @property
    def is_started(self) -> bool:
        """The diretoria started the game: presence, goals and the end of the game open."""
        return self.started_at is not None

    @property
    def is_finished(self) -> bool:
        """The diretoria ended the game, so its fees are payable."""
        return self.finished_at is not None


class EntryStatusChoices(models.TextChoices):
    LISTA = EntryStatus.LISTA.value, "Na lista"
    ESPERA = EntryStatus.ESPERA.value, "Na espera"
    SAIU = EntryStatus.SAIU.value, "Saiu"


class Attendance(models.TextChoices):
    PRESENTE = "presente", "Presente"
    FALTOU = "faltou", "Faltou"


class LateMinutes(models.IntegerChoices):
    """The two lateness tiers the diretoria marks; each one has its own fixed fine."""

    QUARTER = 15, "15 min"
    HALF = 30, "30 min"


class ListEntry(models.Model):
    """A player's sign-up for one Sunday. Mirrors `games.domain.roster.Entry`."""

    game_day = models.ForeignKey(GameDay, on_delete=models.CASCADE, related_name="entries")
    player = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="list_entries"
    )
    position = models.CharField("posição", max_length=10, choices=Position.choices)
    status = models.CharField(max_length=10, choices=EntryStatusChoices.choices)
    joined_at = models.DateTimeField("chegou em")
    confirmed_at = models.DateTimeField("vaga desde", null=True, blank=True)
    bumped = models.BooleanField("perdeu a vaga por prioridade", default=False)
    promoted = models.BooleanField("promovido da espera", default=False)
    board_rank = models.PositiveSmallIntegerField(
        "posição definida pela diretoria",
        null=True,
        blank=True,
        help_text="Lugar na lista ou na espera em que a diretoria pôs o jogador; vale sobre a "
        "ordem automática enquanto ele ficar ali.",
    )
    attendance = models.CharField(
        "presença", max_length=10, choices=Attendance.choices, blank=True, default=""
    )
    attendance_marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    attendance_marked_at = models.DateTimeField(null=True, blank=True)
    late_minutes = models.PositiveSmallIntegerField(
        "atraso", choices=LateMinutes.choices, null=True, blank=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "inscrição"
        verbose_name_plural = "inscrições"
        constraints = [
            models.UniqueConstraint(fields=["game_day", "player"], name="one_entry_per_sunday"),
            models.CheckConstraint(
                condition=(
                    models.Q(status=EntryStatus.LISTA.value, confirmed_at__isnull=False)
                    | (~models.Q(status=EntryStatus.LISTA.value) & models.Q(confirmed_at=None))
                ),
                name="confirmed_entries_have_confirmed_at",
            ),
            models.CheckConstraint(
                condition=models.Q(late_minutes__isnull=True) | ~models.Q(attendance="faltou"),
                name="late_entries_did_not_miss",
            ),
            models.CheckConstraint(
                condition=models.Q(board_rank__isnull=True)
                | (~models.Q(status=EntryStatus.SAIU.value) & models.Q(board_rank__gte=1)),
                name="board_rank_only_on_list_or_waitlist",
            ),
        ]
        indexes = [models.Index(fields=["game_day", "status"])]

    def __str__(self) -> str:
        return f"{self.player} em {self.game_day}"
