"""The turn of the monthly plan: a Sunday belongs to the month of its date, and joining the next
month opens once the month's last Sunday has ended (the diretoria finished it, or at the latest
00:00 of the Monday after), so the next month's mensalistas are in time for its first list."""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from accounts.models import MonthlyEnrollment, Player, PlayerType, plan_month
from core.clock import FixedClock
from core.models import ClubSettings
from finance import charges, monthly
from finance import services as payments
from finance.models import Charge, ChargeKind, ChargeStatus
from finance.selectors import month_report
from games import services as games
from games.domain.types import EntryStatus
from games.models import ListEntry
from games.selectors import build_week_view
from tests.conftest import SP, Sunday, end_game
from tests.integration.test_board_player_management import edit_url

pytestmark = pytest.mark.django_db

OCTOBER = date(2026, 10, 1)
NOVEMBER = date(2026, 11, 1)
DECEMBER = date(2026, 12, 1)
JANUARY = date(2027, 1, 1)
LAST_OCTOBER_SUNDAY = date(2026, 10, 25)
FIRST_NOVEMBER_SUNDAY = date(2026, 11, 1)
HOME = reverse("games:home")
PAYMENTS = reverse("finance:payments")
JOIN = reverse("finance:join_monthly")
PENDING = reverse("board:pending")


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SP)


def monthly_charges(player: Player) -> list[Charge]:
    return list(Charge.objects.filter(player=player, kind=ChargeKind.MONTHLY).order_by("pk"))


def game_charges(player: Player) -> list[Charge]:
    return list(
        Charge.objects.filter(player=player, kind=ChargeKind.GAME).exclude(
            status=ChargeStatus.CANCELLED
        )
    )


def finish(sunday: date, board: Player) -> None:
    """The diretoria starts and finishes `sunday` at the clock's time, as on the Sunday screen."""
    game_day = games.game_day_for(sunday)
    games.start_game_day(game_day.pk, board)
    games.finish_game_day(game_day.pk, board)


def client_for(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


@pytest.fixture
def fees(clock: FixedClock, club_values: ClubSettings) -> ClubSettings:
    return club_values


@pytest.fixture
def player(make_player: Callable[..., Player], president: Player) -> Player:
    return make_player("Garrincha", referred_by=president)


class TestTheLastSundayOfOctober:
    """25/10/2026 is the last Sunday of October; 01/11/2026 is the first of November."""

    def test_october_holds_through_its_last_sunday_until_the_diretoria_finishes_it(
        self, fees: ClubSettings, player: Player, board_member: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        clock.set(at(LAST_OCTOBER_SUNDAY, 10))
        assert plan_month() == OCTOBER
        assert player.is_mensalista

        clock.set(at(LAST_OCTOBER_SUNDAY, 12, 30))
        finish(LAST_OCTOBER_SUNDAY, board_member)

        assert plan_month() == NOVEMBER
        assert not player.is_mensalista
        assert player.effective_type == PlayerType.AVULSO
        enrollment, _ = monthly.join(player)
        assert enrollment.month == NOVEMBER

    def test_nobody_finished_it_so_november_opens_at_monday_midnight(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        clock.set(at(LAST_OCTOBER_SUNDAY, 23, 59))
        assert plan_month() == OCTOBER
        assert player.is_mensalista
        clock.set(at(date(2026, 10, 26), 0))
        assert plan_month() == NOVEMBER
        assert not player.is_mensalista

    def test_joining_in_the_window_opens_one_november_fee_payable_before_the_month(
        self,
        fees: ClubSettings,
        player: Player,
        clock: FixedClock,
        receipt_upload,
        president: Player,
    ) -> None:
        clock.set(at(date(2026, 10, 27), 9))
        enrollment, created = monthly.join(player)

        assert created
        assert enrollment.month == NOVEMBER
        (fee,) = monthly_charges(player)
        assert fee.reference_date == NOVEMBER
        assert fee.amount == Decimal("100.00")
        assert fee.due_date == date(2026, 11, 10)
        assert not fee.is_overdue(date(2026, 10, 27))

        receipt = payments.submit_receipt(fee.pk, player, receipt_upload())
        payments.approve_receipt(receipt.pk, president)
        fee.refresh_from_db()
        assert fee.status == ChargeStatus.PAID
        november = month_report(NOVEMBER, date(2026, 10, 27), with_people=True)
        assert november.collected_monthly == Decimal("100.00")
        assert [m.enrollment.player for m in november.members] == [player]
        assert month_report(OCTOBER, date(2026, 10, 27), with_people=True).members == ()

    def test_an_october_mensalista_keeps_owing_october_and_can_join_november(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        clock.set(at(date(2026, 10, 27), 9))
        assert charges.ensure_monthly_charges() == 0
        monthly.join(player)
        october, november = monthly_charges(player)
        assert (october.reference_date, november.reference_date) == (OCTOBER, NOVEMBER)
        assert october.status == november.status == ChargeStatus.PENDING
        assert player.is_mensalista


class TestTheFirstListOfNovember:
    """The list of 01/11 opens on Monday 26/10 at 8h, already inside the November window."""

    def test_joining_november_then_the_list_counts_as_mensalista(
        self,
        fees: ClubSettings,
        make_player: Callable[..., Player],
        president: Player,
        clock: FixedClock,
    ) -> None:
        guest = make_player("Convidado", player_type=PlayerType.AGREGADO, referred_by=president)
        clock.set(at(date(2026, 10, 26), 8))
        game_day = games.current_game_day()
        assert game_day.date == FIRST_NOVEMBER_SUNDAY
        assert build_week_view(game_day, guest).viewer_type == PlayerType.AGREGADO

        monthly.join(guest)

        # A mensalista signs up on Monday, not on Wednesday like an agregado.
        assert build_week_view(game_day, guest).viewer_type == PlayerType.MENSALISTA
        games.join_list(game_day.pk, guest)
        (row,) = games.entry_rows(game_day)
        assert row.enrolled
        assert ListEntry.objects.get(player=guest).status == EntryStatus.LISTA

        clock.set(at(FIRST_NOVEMBER_SUNDAY, 12, 30))
        end_game(game_day)
        assert game_charges(guest) == []
        assert [c.reference_date for c in monthly_charges(guest)] == [NOVEMBER]

    def test_a_november_mensalista_takes_the_spot_of_the_last_avulso(
        self,
        fees: ClubSettings,
        fill_sunday: Callable[..., Sunday],
        make_player: Callable[..., Player],
        president: Player,
        clock: FixedClock,
    ) -> None:
        clock.set(at(date(2026, 10, 27), 9))
        sunday = fill_sunday(line=20, goalkeepers=2)
        assert sunday.game_day.date == FIRST_NOVEMBER_SUNDAY
        member = make_player("Mensalista de novembro", referred_by=president)
        monthly.join(member)

        games.join_list(sunday.game_day.pk, member)

        assert ListEntry.objects.get(player=member).status == EntryStatus.LISTA
        last = ListEntry.objects.get(player=sunday.line[-1])
        assert (last.status, last.bumped) == (EntryStatus.ESPERA, True)

    def test_an_october_mensalista_who_did_not_join_november_is_avulso_on_the_first(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        clock.set(at(date(2026, 10, 27), 9))
        game_day = games.current_game_day()
        assert build_week_view(game_day, player).viewer_type == PlayerType.AVULSO
        games.join_list(game_day.pk, player)

        clock.set(at(FIRST_NOVEMBER_SUNDAY, 12, 30))
        end_game(game_day)

        (owed,) = game_charges(player)
        assert owed.amount == Decimal("30.00")
        assert owed.reference_date == FIRST_NOVEMBER_SUNDAY

    def test_the_whatsapp_list_puts_the_november_mensalista_with_the_mensalistas(
        self,
        fees: ClubSettings,
        make_player: Callable[..., Player],
        president: Player,
        clock: FixedClock,
    ) -> None:
        clock.set(at(date(2026, 10, 27), 9))
        member = make_player("Bruno B", referred_by=president)
        walk_in = make_player("Gui Hajjar", referred_by=president)
        monthly.join(member)
        game_day = games.current_game_day()
        for each in (member, walk_in):
            games.join_list(game_day.pk, each)

        out = StringIO()
        call_command("lista_whatsapp", stdout=out)

        text = out.getvalue()
        assert text.startswith("Domingo (01/11)")
        assert "Mensalistas ⚽️\n1. Bruno B\n" in text
        assert "AVULSOS\n1. Gui Hajjar\n" in text


class TestScreensInTheWindow:
    def test_the_player_is_offered_november_and_sees_october_still_owed(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        client = client_for(player)
        clock.set(at(date(2026, 10, 27), 9))

        payments_page = client.get(PAYMENTS).content.decode()
        assert "Virar mensalista de novembro" in payments_page
        assert "Vale para os domingos de novembro" in payments_page
        assert "Mensalidade 10/2026" in payments_page
        home = client.get(HOME).content.decode()
        assert "Vire um mensalista" in home
        assert "Virar mensalista de novembro?" in home

        response = client.post(JOIN, follow=True)
        assert "Você é mensalista de novembro! Falta pagar a mensalidade" in (
            response.content.decode()
        )
        assert "Você é mensalista de novembro" in client.get(HOME).content.decode()

    def test_the_mensalista_mark_and_profile_follow_the_plan_month(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        client = client_for(player)
        profile = reverse("community:player", args=[player.pk])
        assert "Meu perfil, mensalista" in client.get(HOME).content.decode()
        assert "Mensalista · Linha" in client.get(profile).content.decode()

        clock.set(at(date(2026, 10, 27), 9))
        assert "Meu perfil, mensalista" not in client.get(HOME).content.decode()
        assert "Avulso · Linha" in client.get(profile).content.decode()

    def test_the_diretoria_sees_and_registers_november(
        self,
        fees: ClubSettings,
        player: Player,
        make_player: Callable[..., Player],
        board_member: Player,
        clock: FixedClock,
    ) -> None:
        old = make_player("Mensalista de outubro")
        monthly.join(old)
        clock.set(at(date(2026, 10, 27), 9))
        monthly.join(player)
        client = client_for(board_member)

        pending = client.get(PENDING).content.decode()
        assert "Mensalistas de novembro" in pending
        assert "1 aderiu · 0 pagaram · 1 pendente." in pending
        assert reverse("board:cash_month", args=["2026-11"]) in pending
        november_cash = client.get(reverse("board:cash_month", args=["2026-11"]))
        assert "Garrincha" in november_cash.content.decode()

        players = client.get(reverse("board:players"), {"tipo": "mensalista"}).content.decode()
        assert "Garrincha" in players
        assert "Mensalista de outubro" not in players

        assert "Mensalista de novembro" in client.get(edit_url(old)).content.decode()


class TestOtherTurns:
    def test_a_five_sunday_month_has_one_fee_and_covers_every_sunday(
        self,
        fees: ClubSettings,
        player: Player,
        board_member: Player,
        clock: FixedClock,
    ) -> None:
        clock.set(at(date(2026, 10, 26), 9))
        monthly.join(player)
        sundays = [date(2026, 11, day) for day in (1, 8, 15, 22, 29)]
        for sunday in sundays:
            clock.set(at(sunday - timedelta(days=5), 9))  # the Tuesday before
            games.join_list(games.game_day_for(sunday).pk, player)
            clock.set(at(sunday, 12, 30))
            assert plan_month() == NOVEMBER
            assert player.is_mensalista
            finish(sunday, board_member)

        assert game_charges(player) == []
        (fee,) = monthly_charges(player)
        assert (fee.reference_date, fee.amount) == (NOVEMBER, Decimal("100.00"))
        assert plan_month() == DECEMBER  # the fifth Sunday was the last one

    def test_a_last_sunday_on_the_last_day_of_the_month(
        self, fees: ClubSettings, player: Player, board_member: Player, clock: FixedClock
    ) -> None:
        # 31/05/2026 is a Sunday: May's window to join June is that Sunday's evening.
        clock.set(at(date(2026, 5, 31), 12, 30))
        assert plan_month() == date(2026, 5, 1)
        finish(date(2026, 5, 31), board_member)
        enrollment, _ = monthly.join(player)
        assert enrollment.month == date(2026, 6, 1)
        assert monthly_charges(player)[0].due_date == date(2026, 6, 10)
        clock.set(at(date(2026, 6, 1), 9))
        assert player.is_mensalista

    def test_the_year_turn(
        self,
        fees: ClubSettings,
        player: Player,
        board_member: Player,
        clock: FixedClock,
    ) -> None:
        clock.set(at(date(2026, 12, 6), 9))
        monthly.join(player)
        clock.set(at(date(2026, 12, 27), 12, 30))
        assert player.is_mensalista
        finish(date(2026, 12, 27), board_member)
        assert not player.is_mensalista

        clock.set(at(date(2026, 12, 28), 9))
        enrollment, _ = monthly.join(player)

        assert enrollment.month == JANUARY
        december, january = monthly_charges(player)
        assert (december.reference_date, january.reference_date) == (DECEMBER, JANUARY)
        assert january.due_date == date(2027, 1, 10)
        game_day = games.current_game_day()
        assert game_day.date == date(2027, 1, 3)
        assert build_week_view(game_day, player).viewer_type == PlayerType.MENSALISTA

    def test_past_enrollments_keep_their_months(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        clock.set(at(date(2026, 10, 27), 9))
        monthly.join(player)
        assert list(
            MonthlyEnrollment.objects.filter(player=player)
            .order_by("month")
            .values_list("month", flat=True)
        ) == [OCTOBER, NOVEMBER]
        assert player.is_enrolled_in(LAST_OCTOBER_SUNDAY)
        assert player.type_in(FIRST_NOVEMBER_SUNDAY) == PlayerType.MENSALISTA
