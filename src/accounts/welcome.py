"""The welcome: a short full-screen introduction shown once before the app, in two sets of four
screens (the player's and the diretoria's), each with its own "seen" timestamp on the player."""

from dataclasses import dataclass

from django.conf import settings
from django.urls import reverse

from accounts.models import Player
from core import clock
from core.models import ClubSettings
from games.domain.schedule import WeekSchedule, upcoming_sunday

STEPS = 4
PLAYER = "player"
BOARD = "board"
_SEEN_FIELD = {PLAYER: "welcome_seen_at", BOARD: "board_welcome_seen_at"}
_URL_NAME = {PLAYER: "accounts:welcome", BOARD: "accounts:welcome_board"}


@dataclass(frozen=True)
class Navigation:
    """Where the buttons of one screen lead."""

    step: int
    previous_url: str | None
    next_url: str | None
    # Last screen only: the way out, and (players of the diretoria) the way on to their set.
    finish_url: str | None
    finish_label: str
    board_url: str | None


def start_url() -> str:
    """The first screen of the player's set: where a first sign-in or sign-up lands."""
    return reverse(_URL_NAME[PLAYER], kwargs={"step": 1})


def board_start_url() -> str:
    return reverse(_URL_NAME[BOARD], kwargs={"step": 1})


def is_seen(player: Player, kind: str) -> bool:
    return getattr(player, _SEEN_FIELD[kind]) is not None


def mark_seen(player: Player, kind: str) -> None:
    """Records the first time a set is opened. Seeing it again changes nothing."""
    field = _SEEN_FIELD[kind]
    if getattr(player, field) is not None:
        return
    now = clock.now()
    # The filter keeps the first moment when two screens open at once.
    Player.objects.filter(pk=player.pk, **{f"{field}__isnull": True}).update(**{field: now})
    setattr(player, field, now)


def exit_url(kind: str) -> str:
    """Where a set ends, and where Pular goes: Início for players, the Painel for the diretoria."""
    return reverse("board:dashboard" if kind == BOARD else "games:home")


def navigation(player: Player, kind: str, step: int) -> Navigation:
    def url(target: int) -> str:
        return reverse(_URL_NAME[kind], kwargs={"step": target})

    last = step == STEPS
    # A new diretoria member reads the player's four screens and is offered their own after them.
    offer_board = last and kind == PLAYER and player.is_board and not is_seen(player, BOARD)
    return Navigation(
        step=step,
        previous_url=url(step - 1) if step > 1 else None,
        next_url=None if last else url(step + 1),
        finish_url=exit_url(kind) if last else None,
        finish_label="Ir para o Painel" if kind == BOARD else "Ir para o Início",
        board_url=board_start_url() if offer_board else None,
    )


def week_context() -> dict[str, object]:
    """The key moments of the coming week, from the same calendar the app runs on, and the
    club's own values: nothing a screen says about times or money is written in the templates."""
    week = WeekSchedule(upcoming_sunday(clock.now()), settings.BABA_LIST_OPENS_AT)
    return {
        "list_opens_at": week.opens_main,
        "agregado_opens_at": week.opens_agregado,
        "last_free_minute": week.last_free_minute,
        "game_starts": week.starts,
        "game_ends": week.ends,
        "club": ClubSettings.load(),
    }
