"""The monthly plan: a player joins for one month and everybody starts over on the 1st."""

import importlib
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from itertools import chain, repeat

import pytest
from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from accounts.models import MonthlyEnrollment, Player, PlayerType
from core.clock import FixedClock
from core.models import ClubSettings
from finance import charges, monthly
from finance import services as payments
from finance.models import Charge, ChargeKind, ChargeStatus
from finance.selectors import month_report
from games import services as games
from games.models import GameDay, ListEntry
from games.selectors import build_week_view
from tests.conftest import SP, end_game
from tests.integration.test_board_player_management import edit_url, form_data

pytestmark = pytest.mark.django_db

OCTOBER = date(2026, 10, 1)
NOVEMBER = date(2026, 11, 1)
FIRST_OF_NOVEMBER = datetime(2026, 11, 1, 8, 0, tzinfo=SP)  # a Sunday
HOME = reverse("games:home")
PAYMENTS = reverse("finance:payments")
JOIN = reverse("finance:join_monthly")
CASH = reverse("board:cash")
PENDING = reverse("board:pending")


def monthly_charges(player: Player) -> list[Charge]:
    return list(Charge.objects.filter(player=player, kind=ChargeKind.MONTHLY).order_by("pk"))


def game_charges(player: Player) -> list[Charge]:
    return list(
        Charge.objects.filter(player=player, kind=ChargeKind.GAME).exclude(
            status=ChargeStatus.CANCELLED
        )
    )


@pytest.fixture
def player(make_player: Callable[..., Player], president: Player) -> Player:
    return make_player("Garrincha", referred_by=president)


@pytest.fixture
def player_client(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def fees(clock: FixedClock, club_values: ClubSettings) -> ClubSettings:
    return club_values


class TestJoining:
    def test_joining_across_midnight_charges_the_enrollment_month(
        self, fees: ClubSettings, player: Player, clock: FixedClock, monkeypatch
    ) -> None:
        before = datetime(2026, 10, 31, 23, 59, 59, 999999, tzinfo=SP)
        after = datetime(2026, 11, 1, 0, 0, tzinfo=SP)
        instants = chain([before], repeat(after))
        monkeypatch.setattr(clock, "now", lambda: next(instants))

        enrollment, created = monthly.join(player)

        assert created
        assert enrollment.month == OCTOBER
        assert enrollment.enrolled_at == before
        (october,) = monthly_charges(player)
        assert october.reference_date == OCTOBER
        assert october.due_date == date(2026, 10, 10)
        assert october.status == ChargeStatus.PENDING
        assert not player.is_mensalista

        november_enrollment, created = monthly.join(player)

        assert created
        assert november_enrollment.month == NOVEMBER
        assert november_enrollment.enrolled_at == after
        october, november = monthly_charges(player)
        assert october.status == ChargeStatus.PENDING
        assert november.reference_date == NOVEMBER
        assert november.due_date == date(2026, 11, 10)
        assert november.status == ChargeStatus.PENDING
        assert player.is_mensalista

    def test_joining_enrolls_for_this_month_and_opens_the_fee(
        self, fees: ClubSettings, player: Player
    ) -> None:
        assert not player.is_mensalista
        enrollment, created = monthly.join(player)

        assert created
        assert enrollment.month == OCTOBER
        assert enrollment.enrolled_by is None
        assert player.is_mensalista
        (charge,) = monthly_charges(player)
        assert charge.status == ChargeStatus.PENDING
        assert charge.amount == Decimal("100.00")
        assert charge.reference_date == OCTOBER

    def test_joining_after_the_due_day_keeps_the_due_day(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        clock.set(datetime(2026, 10, 20, 9, 0, tzinfo=SP))  # the due day is the 10th
        monthly.join(player)
        (charge,) = monthly_charges(player)
        assert charge.due_date == date(2026, 10, 10)
        assert charge.is_overdue(date(2026, 10, 20))

    def test_joining_before_the_due_day_keeps_the_due_day(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        clock.set(datetime(2026, 10, 2, 9, 0, tzinfo=SP))
        monthly.join(player)
        assert monthly_charges(player)[0].due_date == date(2026, 10, 10)

    def test_joining_twice_does_not_charge_twice(self, fees: ClubSettings, player: Player) -> None:
        monthly.join(player)
        _, created = monthly.join(player)
        assert not created
        assert len(monthly_charges(player)) == 1
        assert MonthlyEnrollment.objects.filter(player=player).count() == 1

    def test_the_diretoria_can_join_someone_and_it_is_recorded(
        self, fees: ClubSettings, player: Player, board_member: Player
    ) -> None:
        enrollment, _ = monthly.join(player, actor=board_member)
        assert enrollment.enrolled_by == board_member

    def test_an_inactive_player_cannot_join(self, fees: ClubSettings, player: Player) -> None:
        Player.objects.filter(pk=player.pk).update(is_active=False)
        player.refresh_from_db()
        with pytest.raises(monthly.MonthlyError):
            monthly.join(player)

    def test_with_the_fee_off_joining_still_counts_but_charges_nothing(
        self, clock: FixedClock, player: Player
    ) -> None:
        monthly.join(player)
        assert player.is_mensalista
        assert monthly_charges(player) == []

    def test_paying_later_keeps_the_enrollment(
        self, fees: ClubSettings, player: Player, board_member: Player, receipt_upload, clock
    ) -> None:
        monthly.join(player)
        clock.set(datetime(2026, 10, 24, 18, 0, tzinfo=SP))
        (charge,) = monthly_charges(player)

        receipt = payments.submit_receipt(charge.pk, player, receipt_upload())
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.UNDER_REVIEW
        assert player.is_mensalista

        payments.approve_receipt(receipt.pk, board_member)
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.PAID
        assert player.is_mensalista


class TestTheMonthStartsOver:
    def test_nobody_is_a_mensalista_in_the_new_month(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        clock.set(datetime(2026, 10, 31, 23, 59, tzinfo=SP))
        assert player.is_mensalista
        clock.set(datetime(2026, 11, 1, 0, 0, tzinfo=SP))
        assert not player.is_mensalista
        assert player.effective_type == PlayerType.AVULSO
        assert player.is_enrolled_in(OCTOBER)  # the history stays

    def test_no_fee_is_opened_for_last_months_mensalistas(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        clock.set(FIRST_OF_NOVEMBER)
        assert charges.ensure_monthly_charges() == 0
        assert [c.reference_date for c in monthly_charges(player)] == [OCTOBER]

    def test_an_unpaid_fee_of_the_old_month_is_still_owed(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        clock.set(FIRST_OF_NOVEMBER)
        charges.ensure_monthly_charges()
        (charge,) = monthly_charges(player)
        assert charge.status == ChargeStatus.PENDING
        assert charge.is_overdue(date(2026, 11, 1))

    def test_joining_again_opens_the_new_months_fee_next_to_the_old_one(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        clock.set(FIRST_OF_NOVEMBER)
        monthly.join(player)
        october, november = monthly_charges(player)
        assert (october.reference_date, november.reference_date) == (OCTOBER, NOVEMBER)
        assert player.is_mensalista

    def test_a_standing_agregado_is_an_agregado_again(
        self, fees: ClubSettings, make_player, clock: FixedClock
    ) -> None:
        guest = make_player(player_type=PlayerType.AGREGADO)
        monthly.join(guest)
        assert guest.effective_type == PlayerType.MENSALISTA
        clock.set(FIRST_OF_NOVEMBER)
        assert guest.effective_type == PlayerType.AGREGADO

    def test_the_sunday_after_the_reset_is_charged_like_an_avulso(
        self, fees: ClubSettings, player: Player, make_player, clock: FixedClock
    ) -> None:
        member = make_player("Mensalista de novembro")
        monthly.join(player)  # joined in October only
        clock.set(FIRST_OF_NOVEMBER)
        monthly.join(member)
        game_day = games.game_day_for(date(2026, 11, 1))
        for each in (player, member):
            games.join_list(game_day.pk, each)

        end_game(game_day)

        (owed,) = game_charges(player)
        assert owed.amount == Decimal("30.00")
        assert owed.reference_date == date(2026, 11, 1)
        assert game_charges(member) == []

    def test_a_player_who_joined_this_month_does_not_pay_the_sunday(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        game_day = games.current_game_day()
        games.join_list(game_day.pk, player)
        end_game(game_day)
        assert game_charges(player) == []

    def test_the_week_view_follows_the_month(
        self, fees: ClubSettings, make_player, clock: FixedClock
    ) -> None:
        guest = make_player(player_type=PlayerType.AGREGADO)
        game_day = games.current_game_day()
        assert build_week_view(game_day, guest).viewer_type == PlayerType.AGREGADO
        monthly.join(guest)
        assert build_week_view(game_day, guest).viewer_type == PlayerType.MENSALISTA

    def test_the_old_month_report_keeps_its_members(
        self, fees: ClubSettings, player: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        clock.set(FIRST_OF_NOVEMBER)
        october = month_report(OCTOBER, date(2026, 11, 1), with_people=True)
        november = month_report(NOVEMBER, date(2026, 11, 1), with_people=True)
        assert [m.enrollment.player for m in october.members] == [player]
        assert november.members == ()


class TestMembershipCoverage:
    @pytest.mark.parametrize("next_month", [False, True])
    def test_rejected_withdrawal_preserves_historical_coverage(
        self, fees, player, board_client, board_member, clock, next_month
    ) -> None:
        clock.set(datetime(2026, 10, 1, 9, 0, tzinfo=SP))
        enrollment, _ = monthly.join(player)
        game = games.game_day_for(date(2026, 10, 4))
        games.join_list(game.pk, player)
        clock.set(datetime(2026, 10, 4, 13, 0, tzinfo=SP))
        end_game(game)
        response = board_client.post(edit_url(player), form_data(player, mensalista=""))
        assert response.status_code == 200
        assert "não pode ser retirada" in response.content.decode()
        monthly.join(player)
        enrollment.refresh_from_db()
        assert enrollment.enrolled_at == datetime(2026, 10, 1, 9, 0, tzinfo=SP)
        if next_month:
            clock.set(FIRST_OF_NOVEMBER)
            assert not player.is_mensalista
        games.cancel_game_day(game.pk, board_member, "Chuva")
        games.reopen_game_day(game.pk, board_member)
        games.leave_list(game.pk, player, board_actor=board_member)
        games.join_list(game.pk, player, board_actor=board_member)
        assert game_charges(player) == []
        assert monthly_charges(player)[0].status == ChargeStatus.PENDING

    def test_paid_history_is_preserved(
        self, fees, player, board_client, board_member, receipt_upload
    ):
        monthly.join(player)
        (charge,) = monthly_charges(player)
        receipt = payments.submit_receipt(charge.pk, player, receipt_upload())
        payments.approve_receipt(receipt.pk, board_member)
        board_client.post(edit_url(player), form_data(player, mensalista=""))
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.PAID
        assert player.is_mensalista
        assert charge.receipts.get(pk=receipt.pk).pk == receipt.pk

    def test_membership_can_only_be_waived_explicitly_by_board(self, fees, player, board_member):
        monthly.join(player)
        (charge,) = monthly_charges(player)
        with pytest.raises(PermissionDenied):
            payments.waive_charge(charge.pk, player, "Dispensa")
        payments.waive_charge(charge.pk, board_member, "Dispensa autorizada")
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.WAIVED
        monthly.join(player)
        assert player.is_mensalista
        assert monthly_charges(player) == [charge]


class TestMigration:
    def test_existing_mensalistas_join_the_current_month(
        self, fees: ClubSettings, make_player
    ) -> None:
        migration = importlib.import_module("accounts.migrations.0008_monthly_enrollment")
        old = make_player("Veterano")
        Player.objects.filter(pk=old.pk).update(player_type="mensalista")
        newcomer = make_player("Avulso")

        migration.mensalistas_join_this_month(apps, None)

        old.refresh_from_db()
        assert old.player_type == PlayerType.AVULSO
        assert old.is_mensalista
        assert not newcomer.is_mensalista
        assert MonthlyEnrollment.objects.get(player=old).month == OCTOBER

    @pytest.mark.parametrize("day", [date(2026, 9, 27), date(2026, 10, 11)])
    def test_migrated_coverage_survives_resync(self, fees, make_player, clock, day) -> None:
        migration = importlib.import_module("accounts.migrations.0008_monthly_enrollment")
        old = make_player("Veterano")
        Player.objects.filter(pk=old.pk).update(player_type="mensalista")
        finished = datetime.combine(day, datetime.min.time(), tzinfo=SP)
        game = GameDay.objects.create(date=day, finished_at=finished)
        migration.mensalistas_join_this_month(apps, None)
        newcomer = make_player("Novo")
        monthly.join(newcomer)
        for player in (old, newcomer):
            entry = ListEntry.objects.create(
                game_day=game,
                player=player,
                status="lista",
                joined_at=finished,
                confirmed_at=finished,
            )
            charges.sync_game_charges(game)
            entry.delete()
            charges.sync_game_charges(game)
            ListEntry.objects.create(
                game_day=game,
                player=player,
                status="lista",
                joined_at=finished,
                confirmed_at=finished,
            )
            charges.sync_game_charges(game)
        assert game_charges(old) == []
        assert len(game_charges(newcomer)) == 1

    def test_it_can_be_undone(self, fees: ClubSettings, make_player) -> None:
        migration = importlib.import_module("accounts.migrations.0008_monthly_enrollment")
        player = make_player("Veterano")
        monthly.join(player)
        migration.enrolled_become_mensalistas(apps, None)
        player.refresh_from_db()
        assert player.player_type == "mensalista"


class TestJoinButton:
    def test_joining_goes_to_the_payment_with_the_receipt_form(
        self, fees: ClubSettings, player_client: Client, player: Player
    ) -> None:
        response = player_client.post(JOIN, follow=True)

        assert response.redirect_chain[-1][0] == PAYMENTS
        html = response.content.decode()
        assert "Você é mensalista de outubro" in html
        assert "Mensalidade 10/2026" in html
        assert "Enviar comprovante" in html
        assert player.is_mensalista

    def test_joining_before_midnight_keeps_the_enrolled_month_in_the_response(
        self,
        fees: ClubSettings,
        player_client: Client,
        player: Player,
        clock: FixedClock,
        monkeypatch,
    ) -> None:
        clock.set(datetime(2026, 10, 31, 23, 59, 59, 999999, tzinfo=SP))
        join = monthly.join

        def join_across_midnight(*args, **kwargs):
            result = join(*args, **kwargs)
            clock.set(datetime(2026, 11, 1, 0, 0, tzinfo=SP))
            return result

        monkeypatch.setattr(monthly, "join", join_across_midnight)

        response = player_client.post(JOIN, follow=True)

        assert response.redirect_chain == [(PAYMENTS, 302)]
        html = response.content.decode()
        assert "Você é mensalista de outubro! Falta pagar a mensalidade" in html
        assert "Mensalidade 10/2026" in html
        assert "Enviar comprovante" in html
        assert MonthlyEnrollment.objects.get(player=player).month == OCTOBER
        (charge,) = monthly_charges(player)
        assert charge.reference_date == OCTOBER
        assert charge.status == ChargeStatus.PENDING
        assert not player.is_mensalista

    def test_after_joining_the_home_says_so_and_the_fee_is_overdue(
        self, fees: ClubSettings, player_client: Client
    ) -> None:
        player_client.post(JOIN)
        html = player_client.get(HOME).content.decode()
        assert "Você é mensalista de outubro" in html
        assert "Virar mensalista" not in html
        assert "Pagamento atrasado" in html

    def test_the_button_comes_back_on_the_first_of_the_month(
        self, fees: ClubSettings, player_client: Client, clock: FixedClock
    ) -> None:
        player_client.post(JOIN)
        clock.set(FIRST_OF_NOVEMBER)
        assert "Virar mensalista de novembro" in player_client.get(PAYMENTS).content.decode()
        html = player_client.get(HOME).content.decode()
        assert "Plano mensal" in html
        assert "Pagamento atrasado" in html  # last month's unpaid fee is still owed

    def test_a_double_click_does_not_charge_twice(
        self, fees: ClubSettings, player_client: Client, player: Player
    ) -> None:
        player_client.post(JOIN)
        player_client.post(JOIN)
        assert len(monthly_charges(player)) == 1

    def test_the_button_needs_a_login(self, fees: ClubSettings, client: Client) -> None:
        response = client.post(JOIN)
        assert response.status_code == 302
        assert reverse("accounts:login") in response["Location"]
        assert not MonthlyEnrollment.objects.exists()

    def test_the_button_is_post_only(self, fees: ClubSettings, player_client: Client) -> None:
        assert player_client.get(JOIN).status_code == 405

    def test_nothing_is_offered_while_the_monthly_fee_is_off(
        self, clock: FixedClock, player_client: Client
    ) -> None:
        assert "Virar mensalista" not in player_client.get(HOME).content.decode()


class TestBoardView:
    def test_the_caixa_lists_who_joined_and_where_the_fee_stands(
        self,
        fees: ClubSettings,
        board_client: Client,
        make_player,
        board_member: Player,
        receipt_upload,
    ) -> None:
        paid, pending = make_player("Pagou"), make_player("Deve")
        make_player("Avulso Fixo")
        for each in (paid, pending):
            monthly.join(each)
        receipt = payments.submit_receipt(monthly_charges(paid)[0].pk, paid, receipt_upload())
        payments.approve_receipt(receipt.pk, board_member)

        html = board_client.get(CASH).content.decode()

        assert 'data-section="mensalistas"' in html
        assert "Mensalistas do mês" in html
        assert "Pagou" in html
        assert "Deve" in html
        assert "Avulso Fixo" not in html
        assert "1 pagou · 1 pendente" in html

    def test_the_caixa_of_a_new_month_starts_empty(
        self, fees: ClubSettings, board_client: Client, player: Player, clock: FixedClock
    ) -> None:
        monthly.join(player)
        clock.set(FIRST_OF_NOVEMBER)
        html = board_client.get(CASH).content.decode()
        assert "Ninguém aderiu neste mês." in html

    def test_pendencias_sums_up_the_month_and_leads_to_the_caixa(
        self, fees: ClubSettings, board_client: Client, player: Player
    ) -> None:
        monthly.join(player)
        html = board_client.get(PENDING).content.decode()
        assert "1 aderiu · 0 pagaram · 1 pendente." in html
        assert f'href="{CASH}">Ver no Caixa</a>' in html
        caixa = board_client.get(CASH).content.decode()
        assert "0 pagaram · 1 pendente" in caixa
        assert 'data-section="mensalistas"' in caixa
        assert "Garrincha" in html  # the pending fee is on the list to collect

    def test_the_players_list_shows_and_filters_the_current_mensalistas(
        self, fees: ClubSettings, board_client: Client, player: Player, make_player
    ) -> None:
        make_player("Avulso Fixo")
        monthly.join(player)
        url = reverse("board:players")

        everyone = board_client.get(url).content.decode()
        assert 'badge badge--primary">Mensalista' in everyone

        only = board_client.get(url, {"tipo": "mensalista"}).content.decode()
        assert "Garrincha" in only
        assert "Avulso Fixo" not in only
        avulsos = board_client.get(url, {"tipo": "avulso"}).content.decode()
        assert "Avulso Fixo" in avulsos
        assert "Garrincha" not in avulsos

    def test_the_edit_form_joins_and_rejects_withdrawal(
        self, fees: ClubSettings, board_client: Client, player: Player
    ) -> None:
        board_client.post(edit_url(player), form_data(player, mensalista="on"))
        assert player.is_mensalista
        assert len(monthly_charges(player)) == 1

        board_client.post(edit_url(player), form_data(player, mensalista=""))
        assert player.is_mensalista
        assert monthly_charges(player)[0].status == ChargeStatus.PENDING
        html = board_client.get(PENDING).content.decode()
        assert "não pode ser retirada" in html
        assert reverse("board:waive", args=[monthly_charges(player)[0].pk]) in html

    @pytest.mark.parametrize("enrolled", [False, True])
    def test_director_cannot_change_president_monthly_plan(
        self, fees, board_client, president, enrolled
    ) -> None:
        if enrolled:
            monthly.join(president)
        response = board_client.post(
            edit_url(president), form_data(president, mensalista="" if enrolled else "on")
        )
        assert response.status_code == 302
        assert president.is_mensalista == enrolled
        assert len(monthly_charges(president)) == int(enrolled)
        if enrolled:
            assert monthly_charges(president)[0].status == ChargeStatus.PENDING

    def test_the_reminders_cover_the_monthly_fee(
        self, fees: ClubSettings, board_client: Client, player: Player
    ) -> None:
        monthly.join(player)
        html = board_client.get(reverse("board:reminders")).content.decode()
        assert "Garrincha" in html
        assert "Mensalidade 10/2026" in html
