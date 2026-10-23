from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from accounts.phone import validate_phone
from core import clock
from core.pix import validate_pix_key

ZERO = Decimal("0.00")
# Due day of the monthly fee. Capped at 28 so every month has it.
DUE_DAY_MIN = 1
DUE_DAY_MAX = 28
DEFAULT_DUE_DAY = 10
# Each repeated fine costs this many times the previous one (until the diretoria changes it):
# 50, 75, 112,50, 168,75...
DEFAULT_FINE_FACTOR = Decimal("1.50")
FINE_FACTOR_MIN = Decimal("1.00")
DEFAULT_FINE_BASE = Decimal("50.00")
CENTS = Decimal("0.01")
# Minutes-late tiers of the fixed late fine, and what each costs until the diretoria changes it.
LATE_TIERS = (15, 30)
# Futebois an agregado plays before becoming avulso (until the diretoria changes it).
DEFAULT_AVULSO_AFTER_GAMES = 5
AVULSO_AFTER_GAMES_MIN = 1
PIX_HOLDER_MAX_LENGTH = 80
DEFAULT_LATE_FINE_15 = Decimal("10.00")
DEFAULT_LATE_FINE_30 = Decimal("25.00")


def money_field(verbose_name: str) -> models.DecimalField:
    return models.DecimalField(
        verbose_name,
        max_digits=8,
        decimal_places=2,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
    )


class ClubSettings(models.Model):
    """The single row of values the diretoria configures (amounts are still undecided)."""

    SINGLETON_PK = 1

    monthly_fee = money_field("mensalidade")
    single_fee = money_field("avulso")
    fine_base = models.DecimalField(
        "multa de falta (valor da primeira)",
        max_digits=8,
        decimal_places=2,
        default=DEFAULT_FINE_BASE,
        validators=[MinValueValidator(ZERO)],
    )
    fine_factor = models.DecimalField(
        "multiplicador por falta",
        max_digits=4,
        decimal_places=2,
        default=DEFAULT_FINE_FACTOR,
        validators=[MinValueValidator(FINE_FACTOR_MIN)],
    )
    late_fine_15 = models.DecimalField(
        "multa de atraso de 15 min",
        max_digits=8,
        decimal_places=2,
        default=DEFAULT_LATE_FINE_15,
        validators=[MinValueValidator(ZERO)],
    )
    late_fine_30 = models.DecimalField(
        "multa de atraso de 30 min",
        max_digits=8,
        decimal_places=2,
        default=DEFAULT_LATE_FINE_30,
        validators=[MinValueValidator(ZERO)],
    )
    monthly_due_day = models.PositiveSmallIntegerField(
        "vencimento da mensalidade (dia do mês)",
        default=DEFAULT_DUE_DAY,
        validators=[MinValueValidator(DUE_DAY_MIN), MaxValueValidator(DUE_DAY_MAX)],
    )
    avulso_after_games = models.PositiveSmallIntegerField(
        "futebóis para virar avulso",
        default=DEFAULT_AVULSO_AFTER_GAMES,
        validators=[MinValueValidator(AVULSO_AFTER_GAMES_MIN)],
    )
    pix_key = models.CharField(
        "chave Pix",
        max_length=77,
        blank=True,
        validators=[validate_pix_key],
        help_text="E-mail, celular (+55...), CPF, CNPJ ou chave aleatória.",
    )
    pix_holder = models.CharField(
        "nome do titular do Pix",
        max_length=PIX_HOLDER_MAX_LENGTH,
        blank=True,
        help_text="Em nome de quem está a chave. Opcional: vazio, o app não mostra o titular.",
    )
    board_contact_phone = models.CharField(
        "WhatsApp da diretoria",
        max_length=11,
        blank=True,
        validators=[validate_phone],
    )
    venue_name = models.CharField("local do jogo", max_length=80, blank=True)
    pix_key_changed_at = models.DateTimeField("chave Pix trocada em", null=True, blank=True)
    pix_key_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="chave Pix trocada por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField("atualizado em", auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="atualizado por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "valores"
        verbose_name_plural = "valores"
        constraints = [
            models.CheckConstraint(condition=models.Q(pk=1), name="club_settings_singleton"),
            models.CheckConstraint(
                condition=models.Q(avulso_after_games__gte=AVULSO_AFTER_GAMES_MIN),
                name="avulso_after_games_min",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    monthly_due_day__gte=DUE_DAY_MIN, monthly_due_day__lte=DUE_DAY_MAX
                ),
                name="monthly_due_day_range",
            ),
        ]

    def __str__(self) -> str:
        return "Valores do Babinha"

    @classmethod
    def load(cls) -> "ClubSettings":
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return obj

    def fine_amount_for(self, ordinal: int) -> Decimal:
        """Fine value for the player's n-th counted occurrence: base x factor^(n-1), in cents."""
        if ordinal < 1:
            raise ValueError("ordinal starts at 1")
        amount = self.fine_base * self.fine_factor ** (ordinal - 1)
        return amount.quantize(CENTS, rounding=ROUND_HALF_UP)

    @property
    def late_fine_amounts(self) -> dict[int, Decimal]:
        """The fixed late fine per tier (minutes late). It stays outside the escalation."""
        return {15: self.late_fine_15, 30: self.late_fine_30}

    def fine_examples(self, count: int = 4) -> list[Decimal]:
        """The first fine values, to show how the amount grows."""
        return [self.fine_amount_for(n) for n in range(1, count + 1)]

    @property
    def is_configured(self) -> bool:
        return all(
            value > ZERO
            for value in (
                self.monthly_fee,
                self.single_fee,
                self.fine_base,
            )
        )


class FeedbackKind(models.TextChoices):
    BUG = "bug", "Bug"
    SUGGESTION = "sugestao", "Sugestão"


class Feedback(models.Model):
    """A bug report or suggestion a player sent from "Mais", for the creator to read."""

    MAX_TEXT_LENGTH = 1000

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="enviado por",
        on_delete=models.PROTECT,
        related_name="feedback",
    )
    kind = models.CharField("tipo", max_length=10, choices=FeedbackKind.choices)
    text = models.TextField("mensagem", max_length=MAX_TEXT_LENGTH)
    created_at = models.DateTimeField("enviada em", default=clock.now)

    class Meta:
        verbose_name = "mensagem do app"
        verbose_name_plural = "mensagens do app"
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} de {self.author_id}"
