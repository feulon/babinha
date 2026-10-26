from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import count
from zoneinfo import ZoneInfo

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import MonthlyEnrollment, Player, PlayerType, Position, current_month
from core.clock import FixedClock, use_clock
from core.clock import now as clock_now
from core.models import ClubSettings
from finance import charges
from games import services as games
from games.models import GameDay

SP = ZoneInfo("America/Sao_Paulo")
# A Tuesday morning: the list for Sunday 2026-10-18 is open for mensalistas and avulsos.
TUESDAY = datetime(2026, 10, 13, 9, 0, tzinfo=SP)
KICKOFF_PLUS_5 = datetime(2026, 10, 18, 10, 5, tzinfo=SP)
TEST_PASSWORD = "ab12"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF_BYTES = b"%PDF-1.7\n%comprovante\n"
PIX_KEY = "presidente@baba.com.br"
PIX_HOLDER = "Titular da Conta"  # deliberately not the presidente: the key is not theirs


@pytest.fixture
def clock() -> Iterator[FixedClock]:
    with use_clock(FixedClock(TUESDAY)) as fixed:
        yield fixed


@pytest.fixture
def after_kickoff(clock: FixedClock) -> FixedClock:
    """Move the clock to Sunday 2026-10-18 10h05, with the Sunday started by the diretoria:
    presence, goals and full time count.

    Request it after the fixtures that fill the list, which needs the Tuesday morning clock.
    """
    clock.set(KICKOFF_PLUS_5)
    start_game()
    return clock


@pytest.fixture
def make_player(db: None) -> Callable[..., Player]:
    numbers = count(1)

    def make(
        name: str | None = None,
        *,
        nickname: str | None = None,
        player_type: str = PlayerType.AVULSO,
        position: str = Position.LINHA,
        referred_by: Player | None = None,
        **extra: object,
    ) -> Player:
        n = next(numbers)
        extra.setdefault("phone", f"119{n:08d}")
        extra.setdefault("email", f"jogador{n}@example.com")
        # Fixture players are veterans: only the welcome tests start with it unseen.
        extra.setdefault("welcome_seen_at", TUESDAY)
        extra.setdefault("board_welcome_seen_at", TUESDAY)
        # So are the diretoria: only the tests of the Sunday tutorial start with it unseen.
        extra.setdefault("sunday_tutorial_seen_at", TUESDAY)
        mensalista = player_type == PlayerType.MENSALISTA
        name = name or f"Jogador {n}"
        player = Player.objects.create_player(
            password=TEST_PASSWORD,
            name=name,
            nickname=nickname or name,
            referred_by=referred_by,
            # Mensalista is a month's enrollment, not a stored type: this one joined for the
            # month the test clock is in (no fee opened until the test asks for it).
            player_type=PlayerType.AVULSO if mensalista else player_type,
            position=position,
            **extra,
        )
        if mensalista:
            MonthlyEnrollment.objects.create(player=player, month=current_month())
        return player

    return make


@pytest.fixture
def president(make_player: Callable[..., Player]) -> Player:
    return make_player("Presidente", is_presidente=True)


@pytest.fixture
def board_member(make_player: Callable[..., Player], president: Player) -> Player:
    return make_player("Diretor", is_diretoria=True, referred_by=president)


@pytest.fixture
def club_values(db: None) -> ClubSettings:
    """Fees, fines and a Pix key, as the diretoria would configure them."""
    values = ClubSettings.load()
    values.monthly_fee = Decimal("100.00")
    values.single_fee = Decimal("30.00")
    values.fine_base = Decimal("10.00")
    values.monthly_due_day = 10
    values.pix_key = PIX_KEY
    values.pix_holder = PIX_HOLDER
    values.save()
    return values


@pytest.fixture
def receipt_upload() -> Callable[..., SimpleUploadedFile]:
    def make(content: bytes = PNG_BYTES, name: str = "pix.png") -> SimpleUploadedFile:
        return SimpleUploadedFile(name, content, content_type="application/octet-stream")

    return make


@dataclass(frozen=True)
class Sunday:
    game_day: GameDay
    line: list[Player]
    goalkeepers: list[Player]


@pytest.fixture
def fill_sunday(
    clock: FixedClock, make_player: Callable[..., Player], president: Player
) -> Callable[..., Sunday]:
    """Put `line` line players (potes cycling 1..5) and `goalkeepers` on this Sunday's list."""

    def fill(line: int = 20, goalkeepers: int = 2) -> Sunday:
        game_day = games.current_game_day()
        keepers = [
            make_player(f"Goleiro {n}", position=Position.GOLEIRO, referred_by=president)
            for n in range(1, goalkeepers + 1)
        ]
        players = [
            make_player(f"Linha {n:02d}", pote=(n - 1) % 5 + 1, referred_by=president)
            for n in range(1, line + 1)
        ]
        for player in (*keepers, *players):
            games.join_list(game_day.pk, player)
        return Sunday(game_day, players, keepers)

    return fill


def start_game(game_day: GameDay | None = None) -> None:
    """The diretoria started the Sunday (by default the one of the clock's day) at the clock's
    time: presence, goals and the end of the game open. The clock alone never starts it."""
    if game_day is None:
        game_day = games.game_day_for(clock_now().astimezone(SP).date())
    GameDay.objects.filter(pk=game_day.pk).update(started_at=clock_now())
    game_day.refresh_from_db()


def end_game(game_day: GameDay) -> None:
    """The game is over: the Sunday fees of everyone on the list become payable."""
    charges.finish_game_day(GameDay.objects.get(pk=game_day.pk))
