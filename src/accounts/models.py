from datetime import date

from django.apps import apps
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models.functions import Lower

from accounts.phone import normalize_phone, validate_phone
from core import clock
from games.domain import schedule, types

POTE_MIN = 1
POTE_MAX = 5
DEFAULT_POTE = 3
NICKNAME_MAX_LENGTH = 30


class PlayerType(models.TextChoices):
    MENSALISTA = types.PlayerType.MENSALISTA.value, "Mensalista"
    AVULSO = types.PlayerType.AVULSO.value, "Avulso"
    AGREGADO = types.PlayerType.AGREGADO.value, "Agregado"


# Mensalista is not a standing type: it lasts the Sundays of one month, from the player's
# MonthlyEnrollment, and after the month's last Sunday everybody starts over (see plan_month).
STANDING_TYPES = [
    (PlayerType.AVULSO.value, PlayerType.AVULSO.label),
    (PlayerType.AGREGADO.value, PlayerType.AGREGADO.label),
]


def month_start(day: date) -> date:
    return day.replace(day=1)


def plan_month(today: date | None = None) -> date:
    """The month whose mensalistas count on `today` (default: now), and the one a player joins:
    the month of the next Sunday that has not ended (see games.domain.schedule.plan_month). It is
    the calendar month except after the month's last Sunday, when it is already the next one."""
    today = today or clock.now().date()
    today_ended = (
        today.weekday() == schedule.SUNDAY
        and schedule.is_last_sunday_of_month(today)
        and apps.get_model("games", "GameDay")
        .objects.filter(date=today, finished_at__isnull=False)
        .exists()
    )
    return schedule.plan_month(today, today_ended=today_ended)


class Position(models.TextChoices):
    LINHA = types.Position.LINHA.value, "Linha"
    GOLEIRO = types.Position.GOLEIRO.value, "Goleiro"


class PlayerQuerySet(models.QuerySet["Player"]):
    @staticmethod
    def _enrolled(month: date | None) -> models.Exists:
        return models.Exists(
            MonthlyEnrollment.objects.filter(
                player=models.OuterRef("pk"), month=month or plan_month()
            )
        )

    def with_enrollment(self, month: date | None = None) -> "PlayerQuerySet":
        """Annotate `enrolled`: whether each player is a mensalista in `month` (default: the
        plan month, see `plan_month`)."""
        return self.annotate(enrolled=self._enrolled(month))

    def of_type(self, kind: str, month: date | None = None) -> "PlayerQuerySet":
        """Players whose type in `month` is `kind`: a mensalista outranks the standing type."""
        if kind == PlayerType.MENSALISTA:
            return self.filter(self._enrolled(month))
        return self.filter(~self._enrolled(month), player_type=kind)


class PlayerManager(BaseUserManager["Player"].from_queryset(PlayerQuerySet)):
    def create_player(
        self,
        *,
        phone: str,
        password: str,
        name: str,
        nickname: str,
        referred_by: "Player | None",
        email: str | None = None,
        referrer_text: str = "",
        **extra: object,
    ) -> "Player":
        player = self.model(
            phone=normalize_phone(phone),
            email=self.normalize_email(email).lower() if email else None,
            name=name.strip(),
            nickname=nickname.strip(),
            referred_by=referred_by,
            referrer_text=referrer_text.strip(),
            **extra,
        )
        player.set_password(password)
        player.full_clean(exclude=["password"])
        try:
            with transaction.atomic():
                player.save(using=self._db)
        except IntegrityError as exc:  # a concurrent signup took the same phone or e-mail
            raise ValidationError("Já existe uma conta com este celular ou e-mail.") from exc
        return player

    def create_superuser(
        self, phone: str, password: str, name: str, nickname: str, **extra: object
    ) -> "Player":
        return self.create_player(
            phone=phone,
            password=password,
            name=name,
            nickname=nickname,
            referred_by=None,
            is_superuser=True,
            **extra,
        )

    def find_by_login(self, identifier: str | None) -> "Player":
        """The player who signs in with a phone number (any formatting) or an e-mail."""
        identifier = (identifier or "").strip()
        if "@" in identifier:
            return self.get(email__iexact=identifier)
        try:
            return self.get(phone=normalize_phone(identifier))
        except ValidationError as exc:
            raise self.model.DoesNotExist from exc

    def get_by_natural_key(self, username: str | None) -> "Player":
        # Django's ModelBackend authenticates through this: login accepts phone or e-mail.
        return self.find_by_login(username)


class Player(AbstractBaseUser, PermissionsMixin):
    """A person in the baba. Also the login account."""

    # Both contacts are NULL (never "") when missing, so uniqueness only applies once filled in.
    phone = models.CharField(
        "celular",
        max_length=11,
        unique=True,
        null=True,
        blank=True,
        validators=[validate_phone],
        help_text="Celular com DDD, só os dígitos. É o login do jogador.",
    )
    email = models.EmailField(
        "e-mail", max_length=254, unique=True, null=True, blank=True
    )  # always stored lowercase
    name = models.CharField("nome", max_length=60)
    nickname = models.CharField("apelido", max_length=NICKNAME_MAX_LENGTH)
    referred_by = models.ForeignKey(
        "self",
        verbose_name="indicado por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="referrals",
        help_text="Vazio apenas para a primeira conta (presidente).",
    )
    referrer_text = models.CharField(
        "indicação (texto livre)",
        max_length=60,
        blank=True,
        default="",
        help_text="Quem indicou, como o jogador digitou no cadastro, até ele ligar a um jogador.",
    )
    player_type = models.CharField(
        "tipo",
        max_length=12,
        choices=STANDING_TYPES,
        default=PlayerType.AVULSO,
        help_text="Mensalista não é um tipo fixo: vale para os domingos do mês em que aderiu.",
    )
    position = models.CharField(
        "posição", max_length=10, choices=Position.choices, default=Position.LINHA
    )
    pote = models.PositiveSmallIntegerField(
        "pote",
        default=DEFAULT_POTE,
        validators=[MinValueValidator(POTE_MIN), MaxValueValidator(POTE_MAX)],
    )
    is_diretoria = models.BooleanField("diretoria", default=False)
    is_presidente = models.BooleanField("presidente", default=False)
    is_admin = models.BooleanField(
        "administrador",
        default=False,
        help_text=(
            "Papel oculto do criador do app: tem os poderes do presidente sem aparecer como "
            "presidente. Só se liga pelo banco ou por `manage.py definir_admin`, nunca por tela."
        ),
    )
    is_active = models.BooleanField("ativo", default=True)
    password_is_provisional = models.BooleanField(
        "senha provisória",
        default=False,
        help_text="A diretoria definiu a senha: o jogador é convidado a trocá-la.",
    )
    sunday_tutorial_seen_at = models.DateTimeField(
        "tutorial de montar o domingo visto em",
        null=True,
        blank=True,
        help_text=(
            "Quando a diretoria dispensou o tutorial que aparece na primeira vez que monta o "
            "domingo. Vazio: ainda vai aparecer."
        ),
    )
    created_at = models.DateTimeField("cadastrado em", default=clock.now)
    rules_accepted_at = models.DateTimeField(
        "regras aceitas em",
        null=True,
        blank=True,
        help_text=(
            "Quando marcou que leu as regras no cadastro. Vazio para quem já estava no Babinha."
        ),
    )
    welcome_seen_at = models.DateTimeField(
        "boas-vindas vistas em",
        null=True,
        blank=True,
        help_text="Quando abriu as boas-vindas do jogador. Quem já existia entra como visto.",
    )
    board_welcome_seen_at = models.DateTimeField(
        "boas-vindas da diretoria vistas em",
        null=True,
        blank=True,
        help_text=(
            "Quando abriu as boas-vindas da diretoria, na primeira visita ao Painel. "
            "Quem já era da diretoria entra como visto."
        ),
    )
    player_type_set_at = models.DateTimeField(
        "tipo definido em",
        null=True,
        blank=True,
        help_text="Quando o tipo mudou (a diretoria ou a promoção automática). "
        "Só os futebóis depois disso contam para virar avulso.",
    )
    member_since = models.DateField(
        "no Babinha desde",
        null=True,
        blank=True,
        help_text="Quando entrou no Babinha, se foi antes de ter conta no app. Opcional.",
    )

    photo = models.CharField(
        "foto",
        max_length=200,
        blank=True,
        default="",
        help_text="Nome do arquivo da foto no armazenamento privado (veja accounts.photos).",
    )

    objects = PlayerManager()

    USERNAME_FIELD = "phone"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["name", "nickname"]

    class Meta:
        verbose_name = "jogador"
        verbose_name_plural = "jogadores"
        ordering = [Lower("nickname"), Lower("name")]
        constraints = [
            models.UniqueConstraint(Lower("email"), name="player_email_unique_ci"),
            models.UniqueConstraint(
                fields=["is_presidente"],
                condition=models.Q(is_presidente=True),
                name="single_presidente",
            ),
            models.CheckConstraint(
                condition=models.Q(pote__gte=POTE_MIN, pote__lte=POTE_MAX),
                name="pote_range",
            ),
        ]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        """What other players see: the nickname (the name if there is none)."""
        return self.nickname.strip() or self.name

    @property
    def board_label(self) -> str:
        """For the diretoria's pickers, where homonyms must be told apart: "Apelido (Nome)"."""
        if self.display_name == self.name:
            return self.name
        return f"{self.display_name} ({self.name})"

    def save(self, *args: object, **kwargs: object) -> None:
        # "" would collide with the next player who also left the field blank.
        self.phone = self.phone or None
        self.email = self.email.lower() if self.email else None
        super().save(*args, **kwargs)

    @property
    def is_staff(self) -> bool:
        """The Django admin is the creator's: only a superuser gets in."""
        return self.is_active and self.is_superuser

    @property
    def is_board(self) -> bool:
        """Diretoria, presidente and the hidden admin can do everything the diretoria can."""
        return self.is_active and (self.is_diretoria or self.is_presidente or self.is_admin)

    @property
    def has_presidente_powers(self) -> bool:
        """What only the presidente (and the hidden admin) may do: the Pix key and holder, and
        confirming that a charge was paid. The single place where "is it the presidente" is asked
        about an actor; `is_presidente` alone is the office, which the admin never holds."""
        return self.is_active and (self.is_presidente or self.is_admin)

    @property
    def joined_on(self) -> date:
        """When the player joined the baba: the date the diretoria set, else the account date."""
        return self.member_since or self.created_at.astimezone(clock.SAO_PAULO).date()

    def is_enrolled_in(self, month: date) -> bool:
        """Whether the player joined the monthly plan for the month of the date `month`."""
        return self.enrollments.filter(month=month_start(month)).exists()

    def type_in(self, month: date) -> str:
        """The type that counts in `month`: mensalista only for whoever joined that month."""
        return PlayerType.MENSALISTA.value if self.is_enrolled_in(month) else self.player_type

    @property
    def is_mensalista(self) -> bool:
        """Mensalista now: joined the plan month (see `plan_month`)."""
        return self.is_enrolled_in(plan_month())

    @property
    def effective_type(self) -> str:
        """Type right now. Lists of players annotate `enrolled` instead of asking one by one."""
        return self.type_in(plan_month())

    @property
    def effective_type_label(self) -> str:
        return PlayerType(self.effective_type).label

    @property
    def is_goleiro(self) -> bool:
        return self.position == Position.GOLEIRO


class MonthlyEnrollment(models.Model):
    """A player joined the monthly plan for one month: a mensalista on that month's Sundays.

    Nothing renews by itself: once the month's last Sunday has ended (see `plan_month`) nobody
    has an enrollment for the new month and every player is back to their standing type until
    they join again. The monthly fee is a finance.Charge created when they join, the same for
    any number of Sundays.
    """

    player = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="enrollments")
    month = models.DateField("mês", help_text="Sempre o dia 1.")
    enrolled_at = models.DateTimeField("aderiu em", default=clock.now)
    enrolled_by = models.ForeignKey(
        Player,
        verbose_name="registrado por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Vazio quando o próprio jogador aderiu; a diretoria quando ela registrou.",
    )

    class Meta:
        verbose_name = "adesão mensal"
        verbose_name_plural = "adesões mensais"
        ordering = ["-month", "enrolled_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["player", "month"], name="one_enrollment_per_month"),
            models.CheckConstraint(condition=models.Q(month__day=1), name="enrollment_month_day_1"),
        ]

    def __str__(self) -> str:
        return f"{self.player} - {self.month:%m/%Y}"


class ThrottleEvent(models.Model):
    """One counted event (e.g. a failed login) for rate limiting, keyed by scope and key."""

    scope = models.CharField(max_length=40)
    key = models.CharField(max_length=320)
    at = models.DateTimeField(default=clock.now)

    class Meta:
        indexes = [models.Index(fields=["scope", "key", "at"])]

    def __str__(self) -> str:
        return f"{self.scope}:{self.key}@{self.at.isoformat()}"
