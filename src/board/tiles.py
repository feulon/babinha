"""The Painel of the Diretoria tab: every management area as a tile, grouped by theme, with what
needs attention."""

from dataclasses import dataclass
from decimal import Decimal

from django.urls import reverse

from accounts.models import Player
from core.models import ZERO, ClubSettings
from core.templatetags.money import brl
from games.models import GameDay
from games.selectors import BoardRoster
from matchday.selectors import teams_of

DANGER = "danger"
WAIT = "wait"
INFO = "info"
OK = "ok"


@dataclass(frozen=True)
class Tile:
    title: str
    url: str
    icon: str
    note: str = ""
    tone: str = ""  # DANGER, WAIT, INFO or OK when `note` asks for attention; "" otherwise

    @property
    def needs_attention(self) -> bool:
        return self.tone in (DANGER, WAIT, INFO)


@dataclass(frozen=True)
class Group:
    title: str
    tiles: tuple[Tile, ...]


def _nowrap(text: str) -> str:
    """Keep "R$ 1.555,00" on one line when a tile note wraps."""
    return text.replace(" ", "\u00a0")


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _sunday_tiles(game_day: GameDay, roster: BoardRoster) -> tuple[Tile, ...]:
    day = game_day.date.isoformat()
    teams = teams_of(game_day)
    lineup = teams.lineup
    if game_day.is_cancelled:
        list_note, list_tone = "Cancelado", DANGER
    else:
        waiting = roster.waiting_count
        list_note = f"Linha {len(roster.line)}/{roster.line_capacity}"
        if waiting:
            list_note += f" · {waiting} na espera"
        list_tone = ""
    if lineup is None:
        teams_note, teams_tone = "Sem times", WAIT
    elif not teams.in_sync:
        teams_note, teams_tone = "Ajustar times", WAIT
    elif not lineup.is_confirmed:
        teams_note, teams_tone = "Falta confirmar", WAIT
    else:
        teams_note, teams_tone = "Confirmados", OK
    if game_day.is_cancelled:
        teams_note, teams_tone = "", ""
    return (
        Tile(
            "Montar o domingo",
            reverse("board:game_day", kwargs={"day": day}),
            "list",
            list_note,
            list_tone,
        ),
        Tile("Times", reverse("board:teams", kwargs={"day": day}), "teams", teams_note, teams_tone),
        Tile("Jogos e placares", reverse("board:matches", kwargs={"day": day}), "ball"),
    )


def _receivable_note(open_charges: int, open_total: Decimal, pending_fines: int) -> str:
    if not open_charges:
        return ""
    note = f"{open_charges} em aberto · {_nowrap(brl(open_total))}"
    if pending_fines:
        note += f" · {_count(pending_fines, 'multa', 'multas')}"
    return note


def _money_tiles(
    *,
    receipts_to_review: int,
    open_charges: int,
    open_total: Decimal,
    pending_fines: int,
    club: ClubSettings,
) -> tuple[Tile, ...]:
    configured = bool(club.pix_key) and club.is_configured
    return (
        Tile(
            "Comprovantes",
            reverse("board:receipts"),
            "receipt",
            f"{receipts_to_review} para conferir" if receipts_to_review else "",
            INFO if receipts_to_review else "",
        ),
        Tile(
            "A receber",
            reverse("board:pending"),
            "clock",
            _receivable_note(open_charges, open_total, pending_fines),
            WAIT if open_charges else "",
        ),
        Tile("Caixa", reverse("board:cash"), "chart"),
        Tile(
            "Configurações",
            reverse("board:settings"),
            "pix",
            "" if configured else "Falta configurar",
            "" if configured else WAIT,
        ),
    )


def _player_tiles() -> tuple[Tile, ...]:
    without_phone = Player.objects.filter(phone__isnull=True, is_active=True).count()
    return (
        Tile(
            "Jogadores",
            reverse("board:players"),
            "people",
            f"{without_phone} sem celular" if without_phone else "Cadastro, potes e diretoria",
            WAIT if without_phone else "",
        ),
    )


def build(
    *,
    game_day: GameDay,
    roster: BoardRoster,
    club: ClubSettings,
    receipts_to_review: int,
    open_charges: int,
    pending_fines: int,
    open_total: Decimal = ZERO,
    gold_ball_open: bool = False,
) -> tuple[Group, ...]:
    return (
        Group(f"Domingo {game_day.date:%d/%m}", _sunday_tiles(game_day, roster)),
        Group(
            "Dinheiro",
            _money_tiles(
                receipts_to_review=receipts_to_review,
                open_charges=open_charges,
                open_total=open_total,
                pending_fines=pending_fines,
                club=club,
            ),
        ),
        Group("Jogadores", _player_tiles()),
        Group(
            "Temporada",
            (
                Tile(
                    "Bola de Ouro",
                    reverse("community:gold_ball"),
                    "star",
                    "Votação aberta" if gold_ball_open else "",
                    INFO if gold_ball_open else "",
                ),
                Tile("Temporada", reverse("community:season"), "trophy", "Líderes e encerramento"),
            ),
        ),
    )
