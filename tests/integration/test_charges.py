"""Charges follow the list, the monthly calendar and fines."""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied

from accounts.models import Player, PlayerType, Position
from core.clock import FixedClock
from core.models import ClubSettings
from finance import charges, monthly
from finance import services as payments
from finance.models import Charge, ChargeKind, ChargeStatus
from fines import services as fines
from fines.models import Fine, FineReason, FineStatus
from games import services
from games.domain.roster import ListError
from games.models import Attendance, GameDay
from tests.conftest import SP, end_game, start_game

pytestmark = pytest.mark.django_db

SUNDAY = date(2026, 10, 18)
SATURDAY = date(2026, 10, 17)
SUNDAY_MORNING = datetime(2026, 10, 18, 8, 0, tzinfo=SP)
AFTER_KICKOFF = datetime(2026, 10, 18, 10, 30, tzinfo=SP)


@pytest.fixture
def game_day(clock: FixedClock, club_values: ClubSettings) -> GameDay:
    return services.current_game_day()


def game_charges(player: Player) -> list[Charge]:
    return list(Charge.objects.filter(player=player, kind=ChargeKind.GAME).order_by("pk"))


def active_game_charge(player: Player) -> Charge:
    return Charge.objects.exclude(status=ChargeStatus.CANCELLED).get(
        player=player, kind=ChargeKind.GAME
    )


class TestSundayCharges:
    def test_confirmed_avulso_owes_the_single_fee_by_saturday(
        self, game_day: GameDay, make_player
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        end_game(game_day)
        charge = active_game_charge(player)
        assert charge.status == ChargeStatus.PENDING
        assert charge.amount == Decimal("30.00")
        assert charge.due_date == SATURDAY
        assert charge.reference_date == SUNDAY
        assert charge.game_day == game_day

    def test_agregado_pays_too_but_mensalista_does_not(
        self, game_day: GameDay, make_player, clock: FixedClock
    ) -> None:
        clock.set(datetime(2026, 10, 14, 21, 0, tzinfo=SP))  # Wednesday night
        agregado = make_player(player_type=PlayerType.AGREGADO)
        mensalista = make_player(player_type=PlayerType.MENSALISTA)
        services.join_list(game_day.pk, agregado)
        services.join_list(game_day.pk, mensalista)
        end_game(game_day)
        assert active_game_charge(agregado).amount == Decimal("30.00")
        assert game_charges(mensalista) == []

    def test_waitlisted_player_owes_nothing_until_promoted(
        self, game_day: GameDay, make_player
    ) -> None:
        line = [make_player() for _ in range(22)]
        for player in line:
            services.join_list(game_day.pk, player)
        waiting = make_player()
        services.join_list(game_day.pk, waiting)
        end_game(game_day)
        assert game_charges(waiting) == []

        services.leave_list(game_day.pk, line[0])
        assert active_game_charge(waiting).status == ChargeStatus.PENDING
        assert game_charges(line[0])[0].status == ChargeStatus.CANCELLED

    def test_free_dropout_cancels_and_rejoining_charges_again(
        self, game_day: GameDay, make_player
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        end_game(game_day)
        services.leave_list(game_day.pk, player)
        [cancelled] = game_charges(player)
        assert cancelled.status == ChargeStatus.CANCELLED
        assert cancelled.cancel_reason == charges.LEFT_THE_LIST

        services.join_list(game_day.pk, player)
        assert active_game_charge(player).status == ChargeStatus.PENDING
        assert len(game_charges(player)) == 2

    def test_bumped_by_a_mensalista_loses_the_charge(self, game_day: GameDay, make_player) -> None:
        line = [make_player() for _ in range(22)]
        for player in line:
            services.join_list(game_day.pk, player)
        end_game(game_day)
        services.join_list(game_day.pk, make_player(player_type=PlayerType.MENSALISTA))
        assert game_charges(line[-1])[0].status == ChargeStatus.CANCELLED

    def test_late_dropout_keeps_the_sunday_charge_only_if_money_was_sent(
        self, game_day: GameDay, make_player, receipt_upload, clock: FixedClock
    ) -> None:
        paying, owing = make_player(), make_player()
        for player in (paying, owing):
            services.join_list(game_day.pk, player)
        end_game(game_day)
        payments.submit_receipt(active_game_charge(paying).pk, paying, receipt_upload())

        clock.set(SUNDAY_MORNING)
        services.leave_list(game_day.pk, paying)
        services.leave_list(game_day.pk, owing)
        assert active_game_charge(paying).status == ChargeStatus.UNDER_REVIEW
        assert game_charges(owing)[0].status == ChargeStatus.CANCELLED
        # The late dropout is fined instead.
        assert Charge.objects.get(player=owing, kind=ChargeKind.FINE).amount == Decimal("10.00")

    def test_promoted_on_sunday_morning_is_due_that_day(
        self, game_day: GameDay, make_player, clock: FixedClock
    ) -> None:
        line = [make_player() for _ in range(22)]
        for player in line:
            services.join_list(game_day.pk, player)
        waiting = make_player()
        services.join_list(game_day.pk, waiting)
        end_game(game_day)
        clock.set(SUNDAY_MORNING)
        services.leave_list(game_day.pk, line[0])
        assert active_game_charge(waiting).due_date == SUNDAY

    def test_amount_is_fixed_when_charged(self, game_day: GameDay, make_player) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        end_game(game_day)
        values = ClubSettings.load()
        values.single_fee = Decimal("50.00")
        values.save()
        assert active_game_charge(player).amount == Decimal("30.00")

    def test_nothing_is_charged_while_the_fee_is_zero(self, game_day: GameDay, make_player) -> None:
        values = ClubSettings.load()
        values.single_fee = Decimal("0.00")
        values.save()
        player = make_player()
        services.join_list(game_day.pk, player)
        end_game(game_day)
        assert game_charges(player) == []

    def test_later_enrollment_preserves_old_debt_after_receipt_refusal_and_list_edits(
        self,
        game_day: GameDay,
        make_player,
        clock: FixedClock,
        board_member: Player,
        receipt_upload,
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        clock.set(SUNDAY_CHARGES_OPEN)
        end_game(game_day)
        charge = active_game_charge(player)
        receipt = payments.submit_receipt(charge.pk, player, receipt_upload())
        clock.set(datetime(2026, 10, 20, 9, 0, tzinfo=SP))
        monthly.join(player)
        payments.refuse_receipt(receipt.pk, board_member, "Valor errado")
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.REFUSED
        services.leave_list(game_day.pk, player, board_actor=board_member)
        services.join_list(game_day.pk, player, board_actor=board_member)
        assert active_game_charge(player).status == ChargeStatus.PENDING
        services.cancel_game_day(game_day.pk, board_member, "Chuva")
        services.reopen_game_day(game_day.pk, board_member)
        assert active_game_charge(player).status == ChargeStatus.PENDING

    def test_later_enrollment_does_not_cover_a_late_finalized_sunday(
        self,
        game_day: GameDay,
        make_player,
        clock: FixedClock,
        board_member: Player,
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        clock.set(AFTER_KICKOFF)
        start_game()
        clock.set(datetime(2026, 10, 20, 9, 0, tzinfo=SP))
        monthly.join(player)
        services.finish_game_day(game_day.pk, board_member)
        assert active_game_charge(player).status == ChargeStatus.PENDING

    def test_enrollment_after_early_finish_does_not_cover_that_sunday(
        self,
        game_day: GameDay,
        make_player,
        clock: FixedClock,
        board_member: Player,
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        clock.set(AFTER_KICKOFF)
        start_game()
        services.finish_game_day(game_day.pk, board_member)
        clock.advance(timedelta(minutes=1))
        monthly.join(player)
        services.cancel_game_day(game_day.pk, board_member, "Chuva")
        services.reopen_game_day(game_day.pk, board_member)
        assert active_game_charge(player).status == ChargeStatus.PENDING


SUNDAY_CHARGES_OPEN = datetime(2026, 10, 18, 12, 30, tzinfo=SP)


class TestChargesOpenAfterTheGame:
    """Nobody owes the Sunday fee for being on the list: only once the game is over."""

    def test_joining_the_list_charges_nothing(self, game_day: GameDay, make_player) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        assert game_charges(player) == []

    def test_the_clock_never_ends_the_game_nor_charges(
        self, game_day: GameDay, make_player, clock: FixedClock
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        clock.set(AFTER_KICKOFF)
        start_game()
        for moment in (SUNDAY_CHARGES_OPEN, datetime(2026, 10, 20, 9, 0, tzinfo=SP)):
            clock.set(moment)
            assert game_charges(player) == []
            game_day.refresh_from_db()
            assert not game_day.is_finished

    def test_finishing_charges_everyone_on_the_list_once(
        self, game_day: GameDay, make_player, board_member: Player, clock: FixedClock
    ) -> None:
        avulso, mensalista = make_player(), make_player(player_type=PlayerType.MENSALISTA)
        for player in (avulso, mensalista):
            services.join_list(game_day.pk, player)
        clock.set(AFTER_KICKOFF)
        start_game()
        clock.set(SUNDAY_CHARGES_OPEN + timedelta(hours=1))
        assert services.finish_game_day(game_day.pk, board_member) is True
        charge = active_game_charge(avulso)
        assert charge.status == ChargeStatus.PENDING
        assert charge.due_date == SUNDAY
        assert game_charges(mensalista) == []
        game_day.refresh_from_db()
        assert game_day.finished_at == SUNDAY_CHARGES_OPEN + timedelta(hours=1)

    def test_the_diretoria_can_finish_the_game_early_without_duplicating(
        self, game_day: GameDay, make_player, board_member: Player, clock: FixedClock
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        clock.set(AFTER_KICKOFF)
        start_game()
        assert services.finish_game_day(game_day.pk, board_member) is True
        assert services.finish_game_day(game_day.pk, board_member) is False
        assert len(game_charges(player)) == 1
        game_day.refresh_from_db()
        assert game_day.finished_by == board_member

    def test_cannot_finish_before_the_game_starts_or_when_cancelled(
        self, game_day: GameDay, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(AFTER_KICKOFF)
        with pytest.raises(ListError, match="ainda não começou"):
            services.finish_game_day(game_day.pk, board_member)
        clock.set(AFTER_KICKOFF)
        start_game()
        services.cancel_game_day(game_day.pk, board_member, "Chuva")
        with pytest.raises(ListError, match="cancelado"):
            services.finish_game_day(game_day.pk, board_member)

    def test_only_the_diretoria_finishes_the_game(
        self, game_day: GameDay, make_player, clock: FixedClock
    ) -> None:
        clock.set(AFTER_KICKOFF)
        start_game()
        with pytest.raises(PermissionDenied):
            services.finish_game_day(game_day.pk, make_player())

    def test_a_cancelled_sunday_is_never_charged(
        self, game_day: GameDay, make_player, board_member: Player, clock: FixedClock
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        services.cancel_game_day(game_day.pk, board_member, "Chuva")
        clock.set(SUNDAY_CHARGES_OPEN)
        with pytest.raises(ListError, match="cancelado"):
            services.finish_game_day(game_day.pk, board_member)
        assert game_charges(player) == []

    def test_a_late_dropout_fine_is_unaffected_by_the_game_fee(
        self, game_day: GameDay, make_player, clock: FixedClock
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        clock.set(SUNDAY_MORNING)
        services.leave_list(game_day.pk, player)
        assert Charge.objects.get(player=player, kind=ChargeKind.FINE).amount == Decimal("10.00")
        assert game_charges(player) == []

    def test_dropping_out_after_the_game_cancels_the_unpaid_fee(
        self, game_day: GameDay, make_player, board_member: Player
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        end_game(game_day)
        services.leave_list(game_day.pk, player, board_actor=board_member)
        assert game_charges(player)[0].status == ChargeStatus.CANCELLED


class TestCancelledSunday:
    def test_cancelling_charges_nobody_and_refunds_nothing(
        self,
        game_day: GameDay,
        make_player,
        board_member: Player,
        receipt_upload,
        president: Player,
    ) -> None:
        unpaid, in_review, paid = make_player(), make_player(), make_player()
        for player in (unpaid, in_review, paid):
            services.join_list(game_day.pk, player)
        end_game(game_day)
        payments.submit_receipt(active_game_charge(in_review).pk, in_review, receipt_upload())
        receipt = payments.submit_receipt(active_game_charge(paid).pk, paid, receipt_upload())
        payments.approve_receipt(receipt.pk, president)

        services.cancel_game_day(game_day.pk, board_member, "Chuva")
        assert game_charges(unpaid)[0].status == ChargeStatus.CANCELLED
        assert game_charges(unpaid)[0].cancel_reason == charges.SUNDAY_CANCELLED
        assert active_game_charge(in_review).status == ChargeStatus.UNDER_REVIEW
        assert active_game_charge(paid).status == ChargeStatus.PAID

    def test_refusing_a_receipt_of_a_cancelled_sunday_cancels_the_charge(
        self, game_day: GameDay, make_player, board_member: Player, receipt_upload
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        end_game(game_day)
        receipt = payments.submit_receipt(active_game_charge(player).pk, player, receipt_upload())
        services.cancel_game_day(game_day.pk, board_member, "Chuva")
        payments.refuse_receipt(receipt.pk, board_member, "Valor errado")
        assert game_charges(player)[0].status == ChargeStatus.CANCELLED

    def test_reopening_charges_the_list_again(
        self, game_day: GameDay, make_player, board_member: Player
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        end_game(game_day)
        services.cancel_game_day(game_day.pk, board_member, "Chuva")
        services.reopen_game_day(game_day.pk, board_member)
        assert active_game_charge(player).status == ChargeStatus.PENDING


class TestFineCharges:
    def fine_for(self, game_day: GameDay, player: Player) -> Fine:
        return fines.record_fine(player, game_day, FineReason.LATE_DROPOUT)

    def test_fine_becomes_payable_by_the_next_saturday(
        self, game_day: GameDay, make_player, clock: FixedClock
    ) -> None:
        clock.set(SUNDAY_MORNING)
        fine = self.fine_for(game_day, make_player())
        charge = fine.charge
        assert charge.kind == ChargeKind.FINE
        assert charge.amount == Decimal("10.00")
        assert charge.due_date == date(2026, 10, 24)
        assert charge.reference_date == SUNDAY

    def test_fine_without_value_has_nothing_to_pay(self, game_day: GameDay, make_player) -> None:
        values = ClubSettings.load()
        values.fine_base = Decimal("0.00")
        values.save()
        fine = self.fine_for(game_day, make_player())
        assert not Charge.objects.filter(fine=fine).exists()

    def test_forgiving_cancels_the_charge(
        self, game_day: GameDay, make_player, board_member: Player
    ) -> None:
        fine = self.fine_for(game_day, make_player())
        fines.forgive(fine.pk, board_member, "Avisou no grupo")
        fine.charge.refresh_from_db()
        assert fine.charge.status == ChargeStatus.CANCELLED

    def test_cannot_forgive_while_a_receipt_is_in_review(
        self, game_day: GameDay, make_player, board_member: Player, receipt_upload
    ) -> None:
        player = make_player()
        fine = self.fine_for(game_day, player)
        payments.submit_receipt(fine.charge.pk, player, receipt_upload())
        with pytest.raises(fines.FineError, match="comprovante em análise"):
            fines.forgive(fine.pk, board_member, "Avisou")

    def test_paid_fine_keeps_counting(
        self,
        game_day: GameDay,
        make_player,
        board_member: Player,
        receipt_upload,
        president: Player,
    ) -> None:
        player = make_player()
        fine = self.fine_for(game_day, player)
        receipt = payments.submit_receipt(fine.charge.pk, player, receipt_upload())
        payments.approve_receipt(receipt.pk, president)
        fine.refresh_from_db()
        assert fine.status == FineStatus.PAID
        second = self.fine_for(game_day, player)
        assert second.ordinal == 2
        assert second.amount == Decimal("15.00")

    def test_cancelled_sunday_voids_fines_except_those_in_review(
        self, game_day: GameDay, make_player, board_member: Player, receipt_upload
    ) -> None:
        sent, unsent = make_player(), make_player()
        in_review = self.fine_for(game_day, sent)
        payments.submit_receipt(in_review.charge.pk, sent, receipt_upload())
        unpaid = self.fine_for(game_day, unsent)

        voided = services.cancel_game_day(game_day.pk, board_member, "Chuva")
        assert voided == 1
        unpaid.refresh_from_db()
        in_review.refresh_from_db()
        assert unpaid.status == FineStatus.VOIDED
        assert unpaid.charge.status == ChargeStatus.CANCELLED
        assert in_review.status == FineStatus.PENDING
        assert in_review.charge.status == ChargeStatus.UNDER_REVIEW

        receipt = in_review.charge.receipts.get()
        payments.refuse_receipt(receipt.pk, board_member, "Valor errado")
        in_review.refresh_from_db()
        in_review.charge.refresh_from_db()
        assert in_review.status == FineStatus.VOIDED
        assert in_review.resolution_note == "Domingo cancelado"
        assert in_review.charge.status == ChargeStatus.CANCELLED

    def test_refused_no_show_receipt_after_attendance_was_corrected_voids_the_fine(
        self, game_day: GameDay, make_player, board_member: Player, receipt_upload, clock
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        clock.set(AFTER_KICKOFF)
        start_game()
        fine = services.mark_attendance(game_day.pk, player, Attendance.FALTOU, board_member)
        receipt = payments.submit_receipt(fine.charge.pk, player, receipt_upload())
        services.mark_attendance(game_day.pk, player, Attendance.PRESENTE, board_member)
        fine.refresh_from_db()
        assert fine.status == FineStatus.PENDING

        payments.refuse_receipt(receipt.pk, board_member, "Valor errado")
        fine.refresh_from_db()
        fine.charge.refresh_from_db()
        assert fine.status == FineStatus.VOIDED
        assert fine.resolution_note == "Presença corrigida pela diretoria"
        assert fine.charge.status == ChargeStatus.CANCELLED

    def test_refused_receipt_of_a_fine_that_still_applies_stays_payable(
        self, game_day: GameDay, make_player, board_member: Player, receipt_upload, clock
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        clock.set(AFTER_KICKOFF)
        start_game()
        fine = services.mark_attendance(game_day.pk, player, Attendance.FALTOU, board_member)
        receipt = payments.submit_receipt(fine.charge.pk, player, receipt_upload())

        payments.refuse_receipt(receipt.pk, board_member, "Valor errado")
        fine.refresh_from_db()
        fine.charge.refresh_from_db()
        assert fine.status == FineStatus.PENDING
        assert fine.charge.status == ChargeStatus.REFUSED

    def test_correcting_a_paid_no_show_does_not_fine_twice(
        self,
        game_day: GameDay,
        make_player,
        board_member: Player,
        receipt_upload,
        clock,
        president: Player,
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        clock.set(AFTER_KICKOFF)
        start_game()
        fine = services.mark_attendance(game_day.pk, player, Attendance.FALTOU, board_member)
        receipt = payments.submit_receipt(fine.charge.pk, player, receipt_upload())
        payments.approve_receipt(receipt.pk, president)

        services.mark_attendance(game_day.pk, player, Attendance.PRESENTE, board_member)
        again = services.mark_attendance(game_day.pk, player, Attendance.FALTOU, board_member)
        assert again is None
        assert Fine.objects.filter(player=player).count() == 1
        fine.refresh_from_db()
        assert fine.status == FineStatus.PAID


class TestMonthlyCharges:
    def test_one_charge_per_mensalista_per_month(
        self, clock: FixedClock, club_values: ClubSettings, make_player: Callable[..., Player]
    ) -> None:
        mensalista = make_player(player_type=PlayerType.MENSALISTA)
        make_player(player_type=PlayerType.AVULSO)
        make_player(player_type=PlayerType.MENSALISTA, is_active=False)
        assert charges.ensure_monthly_charges() == 1
        assert charges.ensure_monthly_charges() == 0
        charge = Charge.objects.get(kind=ChargeKind.MONTHLY)
        assert charge.player == mensalista
        assert charge.reference_date == date(2026, 10, 1)
        assert charge.due_date == date(2026, 10, 10)
        assert charge.amount == Decimal("100.00")

    def test_due_on_the_configured_day(
        self, clock: FixedClock, club_values: ClubSettings, make_player
    ) -> None:
        mensalista = make_player()
        clock.set(datetime(2026, 11, 2, 9, 0, tzinfo=SP))
        monthly.join(mensalista)
        charge = Charge.objects.get(player=mensalista, kind=ChargeKind.MONTHLY)
        assert charge.due_date == date(2026, 11, 10)

    def test_waived_month_is_not_charged_again(
        self, clock: FixedClock, club_values: ClubSettings, make_player, board_member: Player
    ) -> None:
        mensalista = make_player(player_type=PlayerType.MENSALISTA)
        charges.ensure_monthly_charges(player=mensalista)
        charge = Charge.objects.get(player=mensalista, kind=ChargeKind.MONTHLY)
        payments.waive_charge(charge.pk, board_member, "Entrou no fim do mês")
        assert charges.ensure_monthly_charges(player=mensalista) == 0
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.WAIVED
        assert charge.cancelled_by == board_member

    def test_not_charged_while_the_fee_is_zero(self, clock: FixedClock, make_player) -> None:
        make_player(player_type=PlayerType.MENSALISTA)
        assert charges.ensure_monthly_charges() == 0

    def test_goleiro_mensalista_pays_the_same(
        self, clock: FixedClock, club_values: ClubSettings, make_player
    ) -> None:
        goleiro = make_player(player_type=PlayerType.MENSALISTA, position=Position.GOLEIRO)
        charges.ensure_monthly_charges(player=goleiro)
        assert Charge.objects.get(player=goleiro).amount == Decimal("100.00")
