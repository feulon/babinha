"""Build the Sunday situations of the demo through the same services the screens call.

Every scenario is the same story told up to a different point, so a scenario is a list of steps
and the later ones start with the steps of the earlier ones. The story, for the upcoming Sunday:

- Last Sunday (history): 11 players, a late dropout fine, a refused Pix, a Pix that was approved.
- This Sunday: mensalistas and avulsos signed up on Monday, agregados on Wednesday, and a late
  mensalista bumped the last agregado, so the waitlist has two people. Then the diretoria draws
  and confirms the teams, the games are played, the game ends, the fees are charged and paid.

Nothing here writes rows directly: if a rule forbids a step, the scenario fails loudly instead of
showing a Sunday the app could never produce. Demo mode (core.demo) is what lets the services
accept these steps on any day and hour.
"""

import random
import struct
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction

from accounts.models import Player, PlayerType
from community import services as community
from community.selectors import eligible_voters
from core import clock
from core.clock import FixedClock, use_clock
from core.models import ClubSettings
from demo.players import DEMO_EMAIL_DOMAIN, DEMO_PASSWORD, PLAYERS
from finance import charges, monthly
from finance import services as payments
from finance.models import Charge, ChargeKind
from fines import services as fines
from fines.models import FineReason
from games import services as games
from games.domain.schedule import upcoming_sunday
from games.domain.types import EntryStatus
from games.models import Attendance, GameDay, ListEntry
from matchday import services as matchday
from matchday.domain.score import Side
from matchday.models import Match

LAST_SUNDAY_LIST = (
    "presidente",
    "paredao",
    "toquinho",
    "diretoria",
    "baixinho",
    "bochecha",
    "magrao",
    "canhota",
    "alemao",
    "formiga",
    "bigode",
)
# Monday sign-ups, in arrival order.
MONDAY_LIST = (
    "presidente",
    "paredao",
    "baixinho",
    "toquinho",
    "diretoria",
    "maodealface",
    "magrao",
    "bochecha",
    "alemao",
    "canhota",
    "bigode",
    "formiga",
    "cabecao",
    "ratinho",
    "dentinho",
    "fumaca",
    "pezao",
    "mineiro",
)
# Wednesday 20h sign-ups. The last one finds the line full and goes to the waitlist.
WEDNESDAY_LIST = ("pastel", "batata", "xerife", "sabia", "chiclete")
# A mensalista arriving late still takes the spot of the last agregado.
LATE_MENSALISTA = "juninho"

DRAW_SEED = 7
# Who gets a late or absent mark in the game, and who votes in the Bola de Ouro.
LATE_15, LATE_30, NO_SHOW = "bigode", "cabecao", "mineiro"
VOTERS = ("diretoria", "baixinho", "magrao", "alemao", "toquinho", "canhota", "dentinho", "pezao")

REVIEW_DELAY = timedelta(hours=2)


@dataclass(frozen=True)
class Login:
    key: str
    role: str


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    steps: tuple[str, ...]
    logins: tuple[Login, ...]


@dataclass(frozen=True)
class SeedResult:
    scenario: Scenario
    sunday: date
    players: dict[str, Player]


def receipt_png() -> bytes:
    """A small, valid PNG standing in for the photo of a Pix receipt."""
    width, height = 120, 200
    row = b"\x00" + bytes((224, 241, 232)) * width
    image = zlib.compress(row * height)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", image) + chunk(b"IEND", b"")
    )


class _Seeder:
    def __init__(self, sunday: date) -> None:
        self.sunday = sunday
        self.players: dict[str, Player] = {}
        self.game_day = games.game_day_for(sunday)

    @property
    def board(self) -> Player:
        return self.players["presidente"]

    @property
    def day_id(self) -> int:
        return self.game_day.pk

    def refresh(self) -> GameDay:
        self.game_day = GameDay.objects.get(pk=self.game_day.pk)
        return self.game_day

    def match_row(self, number: int) -> Match:
        return Match.objects.filter(lineup__game_day=self.game_day).order_by("order")[number - 1]

    # --- the squad and the club -------------------------------------------------------------

    def players_and_values(self) -> None:
        veteran_since = self.sunday - timedelta(days=365)
        for spec in PLAYERS:
            self.players[spec.key] = Player.objects.create_player(
                phone=spec.phone,
                email=spec.email,
                password=DEMO_PASSWORD,
                name=spec.name,
                referred_by=self.players[spec.referred_by] if spec.referred_by else None,
                # Mensalista is a month's enrollment, not a type (see enroll_mensalistas).
                player_type=(
                    PlayerType.AVULSO
                    if spec.player_type == PlayerType.MENSALISTA
                    else spec.player_type
                ),
                position=spec.position,
                pote=spec.pote,
                is_presidente=spec.is_presidente,
                is_diretoria=spec.is_diretoria,
                member_since=None if spec.rookie else veteran_since,
            )
        values = ClubSettings.load()
        values.monthly_fee = Decimal("120.00")
        values.single_fee = Decimal("35.00")
        values.fine_base = Decimal("20.00")
        values.monthly_due_day = 20
        values.pix_key = f"presidente@{DEMO_EMAIL_DOMAIN}"
        values.updated_by = self.board
        values.full_clean()
        values.save()

    def enroll_mensalistas(self) -> None:
        """Whoever the story calls a mensalista pressed the button on the 1st of each month the
        story touches (last Sunday's, today's and this Sunday's), so they paid no single fee."""
        today = clock.now()
        firsts = {
            day.replace(day=1) for day in (today.date(), self.sunday, self.sunday - timedelta(7))
        }
        for first in sorted(firsts):
            moment = datetime.combine(first, time(8, 0), tzinfo=clock.SAO_PAULO)
            with use_clock(FixedClock(moment)):
                for spec in PLAYERS:
                    if spec.player_type == PlayerType.MENSALISTA:
                        monthly.join(self.players[spec.key])

    # --- last Sunday ------------------------------------------------------------------------

    def history(self) -> None:
        sunday = self.sunday - timedelta(days=7)
        game_day = games.game_day_for(sunday)
        noon = datetime.combine(sunday, time(12, 0), tzinfo=clock.SAO_PAULO)
        with use_clock(FixedClock(noon - timedelta(days=5))) as fixed:
            for key in LAST_SUNDAY_LIST:
                games.join_list(game_day.pk, self.players[key])
            payments.save_expense(
                self.board,
                amount=Decimal("650.00"),
                description="Aluguel do campo",
                day=sunday,
            )
            fixed.set(datetime.combine(sunday, time(7, 40), tzinfo=clock.SAO_PAULO))
            games.leave_list(game_day.pk, self.players["formiga"])
            # Demo mode keeps the free period until the teams are out, so the fine a Sunday
            # morning dropout costs is recorded by the same service the list calls.
            fines.record_fine(self.players["formiga"], game_day, FineReason.LATE_DROPOUT)
            fixed.set(noon)
            for key in LAST_SUNDAY_LIST:
                if key != "formiga":
                    games.mark_attendance(
                        game_day.pk, self.players[key], Attendance.PRESENTE, self.board
                    )
            fixed.set(noon + timedelta(minutes=30))
            games.finish_game_day(game_day.pk, self.board)
            fixed.set(noon + timedelta(hours=6))
            self.pay(self.charge_of("toquinho", game_day=game_day), approve=True)
            self.pay(
                self.charge_of("bochecha", game_day=game_day),
                approve=False,
                refusal="O valor do Pix não bate com o do avulso",
            )

    # --- this Sunday: the list --------------------------------------------------------------

    def week_list(self) -> None:
        for key in (*MONDAY_LIST, *WEDNESDAY_LIST, LATE_MENSALISTA):
            games.join_list(self.day_id, self.players[key])

    def monthly_fees(self) -> None:
        charges.ensure_monthly_charges()
        month = charges.month_start(clock.now().date())
        for key, approve in (("presidente", True), ("diretoria", True), ("paredao", None)):
            charge = Charge.objects.get(
                player=self.players[key], kind=ChargeKind.MONTHLY, reference_date=month
            )
            self.pay(charge, approve=approve)

    # --- this Sunday: the teams -------------------------------------------------------------

    def draw_teams(self) -> None:
        matchday.draw(self.day_id, self.board, random.Random(DRAW_SEED))

    def confirm_teams(self) -> None:
        matchday.confirm(self.day_id, self.board)

    # --- this Sunday: the games and the attendance ------------------------------------------

    def presence(self) -> None:
        """The attendance as the diretoria marks it during the games."""
        on_list = [row.player for row in self.entries()]
        for player in on_list:
            games.mark_attendance(self.day_id, player, Attendance.PRESENTE, self.board)
        games.mark_attendance(self.day_id, self.players[NO_SHOW], Attendance.FALTOU, self.board)
        games.mark_late(self.day_id, self.players[LATE_15], 15, self.board)
        games.mark_late(self.day_id, self.players[LATE_30], 30, self.board)

    def entries(self) -> list[ListEntry]:
        return list(
            ListEntry.objects.filter(game_day=self.game_day, status=EntryStatus.LISTA.value)
            .select_related("player")
            .order_by("confirmed_at", "player_id")
        )

    def score(self, number: int, home: int, away: int) -> None:
        """Take match `number` to the given score, adding the goals it still lacks, each with a
        scorer and sometimes an assist."""
        match = self.match_row(number)
        current = matchday.score_of(match)
        # The side with fewer goals scores first: two goals end a match, so the winner is last.
        sides = [(Side.HOME, home - current.home), (Side.AWAY, away - current.away)]
        for side, missing in sorted(sides, key=lambda pair: pair[1]):
            scorers = matchday.side_scorers(match, side)
            for n in range(missing):
                scorer = scorers[n % len(scorers)]
                mates = [p for p in scorers if p.pk != scorer.pk]
                matchday.add_goal(
                    match.pk,
                    self.board,
                    side=side,
                    scorer=scorer.pk,
                    assist=mates[n % len(mates)].pk if n % 2 == 0 else None,
                    assist_not_applicable=n % 2 == 1,
                )

    def play(self, home: int, away: int, *, full_time: bool = False) -> int:
        """Call the next match from the waiting line and take it to the score. A score short of
        two goals stays on court unless `full_time` closes it. Returns the match number."""
        number = matchday.start_next_match(self.day_id, self.board).order
        self.score(number, home, away)
        if full_time:
            matchday.finish_match(self.match_row(number).pk, self.board)
        return number

    def games_under_way(self) -> None:
        """Two games over (one won, one drawn), the third on court with a goal: the winner of the
        first stayed, and the drawn game sent both teams out."""
        self.presence()
        self.play(2, 1)
        self.play(1, 1, full_time=True)
        self.play(1, 0)

    def games_over(self) -> None:
        """Six games played through the waiting line, the last two drawn at full time."""
        self.score(3, 2, 0)
        self.play(0, 2)
        self.play(1, 1, full_time=True)
        self.play(1, 1, full_time=True)
        games.finish_game_day(self.day_id, self.board)

    def some_votes(self) -> None:
        """Part of the group voted in the Bola de Ouro; the presidente did not."""
        self.vote(self.players[key] for key in VOTERS)

    def all_votes_but_one(self) -> None:
        """Everyone who played voted except the presidente: their vote closes the Bola de Ouro."""
        self.refresh()
        voters = [p for p in eligible_voters(self.game_day) if p.pk != self.board.pk]
        self.vote(voters)

    def vote(self, voters) -> None:
        self.refresh()
        nominees = [p.pk for p in eligible_voters(self.game_day)]
        for n, voter in enumerate(voters):
            picks = [pk for pk in nominees if pk != voter.pk]
            # Three different nominees that change from voter to voter, so the table has a podium.
            first, second, third = (picks[(n * 3 + k * 7) % len(picks)] for k in range(3))
            if len({first, second, third}) < 3:
                first, second, third = picks[0], picks[1], picks[2]
            community.cast_vote(self.day_id, voter, first=first, second=second, third=third)

    # --- this Sunday: the fees --------------------------------------------------------------

    def charge_and_pay(self) -> None:
        """The game ends: everyone owes. The late arrivals are fined; Pix in every state."""
        games.fine_late_arrivals(self.day_id, self.board)
        games.finish_game_day(self.day_id, self.board)
        self.refresh()
        self.pay(self.charge_of("fumaca", game_day=self.game_day), approve=True)
        self.pay(self.charge_of("ratinho", game_day=self.game_day), approve=True)
        self.pay(
            self.charge_of("batata", game_day=self.game_day),
            approve=False,
            refusal="O comprovante não mostra o valor",
        )
        self.pay(self.charge_of("canhota", game_day=self.game_day), approve=None)
        self.pay(self.charge_of("pastel", game_day=self.game_day), approve=None)

    # --- edge cases -------------------------------------------------------------------------

    def one_goalkeeper(self) -> None:
        """21 line players and a single goalkeeper: one of the line plays in goal."""
        games.leave_list(self.day_id, self.players["maodealface"], board_actor=self.board)
        games.join_list(self.day_id, self.players["tatu"])

    def a_rookie(self) -> None:
        """Tatu, who never played here, takes Pastel's spot on Fumaça's invitation: the teams
        rule keeps them together."""
        games.leave_list(self.day_id, self.players["chiclete"])
        games.leave_list(self.day_id, self.players["sabia"])
        games.leave_list(self.day_id, self.players["pastel"], board_actor=self.board)
        games.join_list(self.day_id, self.players["tatu"])

    def late_dropout(self) -> None:
        """Formiga drops out after the teams are confirmed: a fine, and the team is incomplete."""
        games.leave_list(self.day_id, self.players["formiga"])

    def cancelled(self) -> None:
        games.cancel_game_day(self.day_id, self.board, "Chuva forte: o campo está alagado")

    # --- shared helpers ---------------------------------------------------------------------

    def pay(self, charge: Charge, *, approve: bool | None, refusal: str = "") -> None:
        """Send a receipt now; then approve it, refuse it or leave it (None) under review."""
        upload = SimpleUploadedFile("comprovante-pix.png", receipt_png())
        receipt = payments.submit_receipt(charge.pk, charge.player, upload)
        if approve is None:
            return
        if approve:
            payments.approve_receipt(receipt.pk, self.board)
        else:
            payments.refuse_receipt(receipt.pk, self.board, refusal)

    def charge_of(self, key: str, **filters: object) -> Charge:
        return Charge.objects.get(
            player=self.players[key], kind=ChargeKind.GAME, status="pendente", **filters
        )


BASE = ("players_and_values", "enroll_mensalistas", "history", "week_list", "monthly_fees")

_PRESIDENTE = Login("presidente", "presidente e diretoria: conduz o domingo pela aba Diretoria")
_PLAYER_ON_LIST = Login("baixinho", "mensalista na lista, com time quando os times saem")
_OUT = Login("tatu", "avulso fora da lista: entra e cai na espera")
_KEEPER_OUT = Login("gatinho", "goleiro agregado fora da lista: ainda tem vaga de goleiro")
_TOP_OF_WAIT = Login("sabia", "agregado que perdeu a vaga para um mensalista (topo da espera)")


def _scenario(key: str, title: str, extra: Sequence[str], logins: Sequence[Login] = ()) -> Scenario:
    return Scenario(key, title, (*BASE, *extra), (_PRESIDENTE, *logins))


_TEAMS_OUT = ("draw_teams",)
_TEAMS_IN = (*_TEAMS_OUT, "confirm_teams")
_GAMES = (*_TEAMS_IN, "games_under_way")
_OVER = (*_GAMES, "games_over")
_CHARGED = (*_OVER, "some_votes", "charge_and_pay")

SCENARIOS: tuple[Scenario, ...] = (
    _scenario(
        "lista",
        "Lista cheia com espera: 20 de linha, 2 goleiros e dois na espera",
        (),
        (_PLAYER_ON_LIST, _OUT, _KEEPER_OUT, _TOP_OF_WAIT),
    ),
    _scenario(
        "times-sorteados",
        "Times sorteados, ainda sem confirmar",
        _TEAMS_OUT,
        (_PLAYER_ON_LIST,),
    ),
    _scenario(
        "times-confirmados",
        "Times confirmados: os jogadores já veem o seu time",
        _TEAMS_IN,
        (_PLAYER_ON_LIST, _OUT),
    ),
    _scenario(
        "jogos-em-andamento",
        "Jogos em andamento: dois encerrados, um em quadra, presença e atrasos marcados",
        _GAMES,
        (_PLAYER_ON_LIST,),
    ),
    _scenario(
        "partida-encerrada",
        "Todas as partidas encerradas e a Bola de Ouro aberta (parte já votou)",
        (*_OVER, "some_votes"),
        (
            Login("presidente", "presidente: ainda não votou na Bola de Ouro"),
            Login("diretoria", "diretoria: já votou"),
        ),
    ),
    _scenario(
        "bola-de-ouro-ultimo-voto",
        "Bola de Ouro aberta e só falta o voto do presidente: ao votar, o resultado aparece",
        (*_OVER, "all_votes_but_one"),
        (Login("presidente", "presidente: o último voto, que fecha a votação"),),
    ),
    _scenario(
        "cobranca",
        "Jogo finalizado: cobrança aberta com Pix pendente, pago e recusado e uma multa",
        _CHARGED,
        (
            Login("canhota", "comprovante em análise"),
            Login("toquinho", "Pix ainda não enviado (pendente)"),
            Login("batata", "comprovante recusado: envia outro"),
            Login("mineiro", "faltou: multa pendente"),
            Login("bigode", "chegou 15 min atrasado: multa de atraso"),
        ),
    ),
    _scenario(
        "borda-um-goleiro",
        "Borda: 21 de linha e um só goleiro, então um jogador da linha vai ao gol",
        ("one_goalkeeper", *_TEAMS_OUT),
    ),
    _scenario(
        "borda-novato",
        "Borda: Tatu joga pela primeira vez e fica no time de quem o convidou (Fumaça)",
        ("a_rookie", *_TEAMS_OUT),
        (_OUT,),
    ),
    _scenario(
        "borda-desistencia-tardia",
        "Borda: desistência depois dos times confirmados (multa e time incompleto)",
        (*_TEAMS_IN, "late_dropout"),
        (Login("formiga", "desistiu depois dos times: multa pendente"),),
    ),
    _scenario("borda-cancelado", "Borda: domingo cancelado pela diretoria", ("cancelled",)),
)
BY_KEY = {scenario.key: scenario for scenario in SCENARIOS}


def _steps(seeder: _Seeder) -> dict[str, Callable[[], None]]:
    return {
        "players_and_values": seeder.players_and_values,
        "enroll_mensalistas": seeder.enroll_mensalistas,
        "history": seeder.history,
        "week_list": seeder.week_list,
        "monthly_fees": seeder.monthly_fees,
        "draw_teams": seeder.draw_teams,
        "confirm_teams": seeder.confirm_teams,
        "games_under_way": seeder.games_under_way,
        "games_over": seeder.games_over,
        "some_votes": seeder.some_votes,
        "all_votes_but_one": seeder.all_votes_but_one,
        "charge_and_pay": seeder.charge_and_pay,
        "one_goalkeeper": seeder.one_goalkeeper,
        "a_rookie": seeder.a_rookie,
        "late_dropout": seeder.late_dropout,
        "cancelled": seeder.cancelled,
    }


def seed(key: str) -> SeedResult:
    """Create the scenario in an empty database (demo mode on)."""
    scenario = BY_KEY[key]
    sunday = upcoming_sunday(clock.now())
    seeder = _Seeder(sunday)
    steps = _steps(seeder)
    with transaction.atomic():
        for name in scenario.steps:
            steps[name]()
    return SeedResult(scenario, sunday, seeder.players)
