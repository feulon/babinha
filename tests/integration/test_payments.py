"""Receipt submission, the diretoria's review, waivers, expenses and the month report."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import storages

from accounts.models import Player, PlayerType
from core.clock import FixedClock
from core.models import ClubSettings
from finance import charges, receipt_files
from finance import services as payments
from finance.models import (
    Charge,
    ChargeKind,
    ChargeStatus,
    Expense,
    PaymentMethod,
    Receipt,
    ReceiptStatus,
)
from finance.selectors import late_mensalistas, month_report, open_charges, player_payments
from fines import services as fines
from fines.models import FineReason, FineStatus
from games import services
from games.models import GameDay
from tests.conftest import PDF_BYTES, end_game

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 13)


@pytest.fixture
def game_day(clock: FixedClock, club_values: ClubSettings) -> GameDay:
    return services.current_game_day()


@pytest.fixture
def player(make_player, president: Player) -> Player:
    return make_player("Garrincha", referred_by=president)


@pytest.fixture
def charge(game_day: GameDay, player: Player) -> Charge:
    services.join_list(game_day.pk, player)
    end_game(game_day)
    return Charge.objects.get(player=player, kind=ChargeKind.GAME)


class TestSubmit:
    def test_receipt_is_stored_privately_and_charge_goes_under_review(
        self, charge: Charge, player: Player, receipt_upload
    ) -> None:
        receipt = payments.submit_receipt(charge.pk, player, receipt_upload(PDF_BYTES, "pix.pdf"))
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.UNDER_REVIEW
        assert receipt.status == ReceiptStatus.UNDER_REVIEW
        assert receipt.content_type == "application/pdf"
        assert receipt.original_name == "pix.pdf"
        assert receipt.size == len(PDF_BYTES)
        assert receipt.file.name.startswith("comprovantes/2026/10/")
        assert receipt.file.name.endswith(".pdf")
        assert storages["receipts"].open(receipt.file.name).read() == PDF_BYTES

    def test_only_the_owner_can_pay(self, charge: Charge, make_player, receipt_upload) -> None:
        with pytest.raises(PermissionDenied):
            payments.submit_receipt(charge.pk, make_player(), receipt_upload())

    def test_invalid_file_is_rejected_before_anything_is_stored(
        self, charge: Charge, player: Player, receipt_upload
    ) -> None:
        with pytest.raises(receipt_files.ReceiptFileError):
            payments.submit_receipt(charge.pk, player, receipt_upload(b"<svg/>", "x.svg"))
        assert not Receipt.objects.exists()
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.PENDING

    def test_a_new_receipt_replaces_the_one_under_review(
        self, charge: Charge, player: Player, receipt_upload
    ) -> None:
        first = payments.submit_receipt(charge.pk, player, receipt_upload())
        second = payments.submit_receipt(charge.pk, player, receipt_upload())
        first.refresh_from_db()
        charge.refresh_from_db()
        assert first.status == ReceiptStatus.SUPERSEDED
        assert second.status == ReceiptStatus.UNDER_REVIEW
        assert charge.status == ChargeStatus.UNDER_REVIEW
        assert list(Receipt.objects.filter(status=ReceiptStatus.UNDER_REVIEW)) == [second]

    def test_a_superseded_receipt_can_no_longer_be_reviewed(
        self, charge: Charge, player: Player, board_member: Player, receipt_upload
    ) -> None:
        first = payments.submit_receipt(charge.pk, player, receipt_upload())
        payments.submit_receipt(charge.pk, player, receipt_upload())
        with pytest.raises(payments.PaymentError, match="outro comprovante"):
            payments.approve_receipt(first.pk, board_member)
        with pytest.raises(payments.PaymentError, match="outro comprovante"):
            payments.refuse_receipt(first.pk, board_member, "Ilegível")

    def test_a_paid_charge_cannot_be_replaced(
        self, charge: Charge, player: Player, board_member: Player, receipt_upload
    ) -> None:
        receipt = payments.submit_receipt(charge.pk, player, receipt_upload())
        payments.approve_receipt(receipt.pk, board_member)
        with pytest.raises(payments.PaymentError, match="já está paga"):
            payments.submit_receipt(charge.pk, player, receipt_upload())

    def test_cancelled_charge_cannot_be_paid(
        self, charge: Charge, player: Player, game_day: GameDay, receipt_upload
    ) -> None:
        services.leave_list(game_day.pk, player)
        with pytest.raises(payments.PaymentError, match="cancelada"):
            payments.submit_receipt(charge.pk, player, receipt_upload())


class TestReview:
    def test_approve_marks_paid(
        self, charge: Charge, player: Player, board_member: Player, receipt_upload, clock
    ) -> None:
        receipt = payments.submit_receipt(charge.pk, player, receipt_upload())
        payments.approve_receipt(receipt.pk, board_member)
        charge.refresh_from_db()
        receipt.refresh_from_db()
        assert charge.status == ChargeStatus.PAID
        assert charge.paid_at == clock.now()
        assert receipt.status == ReceiptStatus.APPROVED
        assert receipt.reviewed_by == board_member

    def test_refuse_needs_a_reason_and_lets_the_player_resend(
        self, charge: Charge, player: Player, board_member: Player, receipt_upload
    ) -> None:
        first = payments.submit_receipt(charge.pk, player, receipt_upload())
        with pytest.raises(payments.PaymentError, match="motivo"):
            payments.refuse_receipt(first.pk, board_member, "   ")
        payments.refuse_receipt(first.pk, board_member, "Valor diferente do cobrado")
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.REFUSED

        [row] = player_payments(player, TODAY).open
        assert row.refusal_reason == "Valor diferente do cobrado"
        assert row.charge.is_payable

        second = payments.submit_receipt(charge.pk, player, receipt_upload())
        payments.approve_receipt(second.pk, board_member)
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.PAID
        assert list(charge.receipts.values_list("status", flat=True)) == [
            ReceiptStatus.APPROVED,
            ReceiptStatus.REFUSED,
        ]

    def test_a_receipt_is_reviewed_once(
        self, charge: Charge, player: Player, board_member: Player, receipt_upload
    ) -> None:
        receipt = payments.submit_receipt(charge.pk, player, receipt_upload())
        payments.approve_receipt(receipt.pk, board_member)
        with pytest.raises(payments.PaymentError, match="já foi analisado"):
            payments.refuse_receipt(receipt.pk, board_member, "Engano")
        with pytest.raises(payments.PaymentError, match="já foi analisado"):
            payments.approve_receipt(receipt.pk, board_member)

    def test_only_the_board_reviews(self, charge: Charge, player: Player, receipt_upload) -> None:
        receipt = payments.submit_receipt(charge.pk, player, receipt_upload())
        with pytest.raises(PermissionDenied):
            payments.approve_receipt(receipt.pk, player)
        with pytest.raises(PermissionDenied):
            payments.refuse_receipt(receipt.pk, player, "Não")

    def test_refused_after_leaving_the_list_is_cancelled(
        self,
        charge: Charge,
        player: Player,
        game_day: GameDay,
        board_member: Player,
        receipt_upload,
    ) -> None:
        receipt = payments.submit_receipt(charge.pk, player, receipt_upload())
        services.leave_list(game_day.pk, player)
        payments.refuse_receipt(receipt.pk, board_member, "Ilegível")
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.CANCELLED


class TestUndoApproval:
    def test_a_fresh_confirmation_goes_back_to_review(
        self, charge: Charge, player: Player, board_member: Player, receipt_upload, clock
    ) -> None:
        receipt = payments.submit_receipt(charge.pk, player, receipt_upload())
        payments.approve_receipt(receipt.pk, board_member)
        clock.advance(payments.UNDO_WINDOW)
        payments.undo_approval(receipt.pk, board_member)
        charge.refresh_from_db()
        receipt.refresh_from_db()
        assert charge.status == ChargeStatus.UNDER_REVIEW
        assert charge.paid_at is None
        assert receipt.status == ReceiptStatus.UNDER_REVIEW
        assert receipt.reviewed_by is None
        payments.approve_receipt(receipt.pk, board_member)  # can be reviewed again

    def test_a_late_undo_is_refused(
        self, charge: Charge, player: Player, board_member: Player, receipt_upload, clock
    ) -> None:
        receipt = payments.submit_receipt(charge.pk, player, receipt_upload())
        payments.approve_receipt(receipt.pk, board_member)
        clock.advance(payments.UNDO_WINDOW + timedelta(seconds=1))
        with pytest.raises(payments.PaymentError, match="10 minutos"):
            payments.undo_approval(receipt.pk, board_member)
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.PAID

    def test_undoing_a_paid_fine_makes_it_pending_again(
        self, game_day: GameDay, player: Player, board_member: Player, receipt_upload
    ) -> None:
        fine = fines.record_fine(player, game_day, FineReason.NO_SHOW)
        receipt = payments.submit_receipt(fine.charge.pk, player, receipt_upload())
        payments.approve_receipt(receipt.pk, board_member)
        payments.undo_approval(receipt.pk, board_member)
        fine.refresh_from_db()
        assert fine.status == FineStatus.PENDING


class TestMarkPaid:
    def test_cash_payment_enters_the_cash_book_with_its_audit_trail(
        self, charge: Charge, board_member: Player, clock
    ) -> None:
        payments.mark_paid(charge.pk, board_member, PaymentMethod.CASH, " na quadra ")
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.PAID
        assert charge.paid_at == clock.now()
        assert charge.payment_method == PaymentMethod.CASH
        assert charge.payment_note == "na quadra"
        assert charge.marked_paid_by == board_member
        report = month_report(charge.reference_date, TODAY, with_people=False)
        assert report.collected == charge.amount
        assert report.pending_total == Decimal("0.00")

    def test_a_refused_charge_can_be_marked_paid(
        self, charge: Charge, player: Player, board_member: Player, receipt_upload
    ) -> None:
        receipt = payments.submit_receipt(charge.pk, player, receipt_upload())
        payments.refuse_receipt(receipt.pk, board_member, "Ilegível")
        payments.mark_paid(charge.pk, board_member, PaymentMethod.PIX)
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.PAID

    def test_a_paid_fine_is_marked_paid_too(
        self, game_day: GameDay, player: Player, board_member: Player
    ) -> None:
        fine = fines.record_fine(player, game_day, FineReason.NO_SHOW)
        payments.mark_paid(fine.charge.pk, board_member, PaymentMethod.CASH)
        fine.refresh_from_db()
        assert fine.status == FineStatus.PAID

    def test_rejects_unknown_method_closed_charges_and_players(
        self, charge: Charge, player: Player, board_member: Player
    ) -> None:
        with pytest.raises(payments.PaymentError, match="forma de pagamento"):
            payments.mark_paid(charge.pk, board_member, "cheque")
        with pytest.raises(PermissionDenied):
            payments.mark_paid(charge.pk, player, PaymentMethod.CASH)
        payments.mark_paid(charge.pk, board_member, PaymentMethod.CASH)
        with pytest.raises(payments.PaymentError, match="paga"):
            payments.mark_paid(charge.pk, board_member, PaymentMethod.CASH)


class TestWaive:
    def test_waive_by_hand(self, charge: Charge, board_member: Player) -> None:
        payments.waive_charge(charge.pk, board_member, "Machucado")
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.WAIVED
        assert charge.cancel_reason == "Machucado"

    def test_fines_are_forgiven_not_waived(
        self, game_day: GameDay, player: Player, board_member: Player
    ) -> None:
        fine = fines.record_fine(player, game_day, FineReason.NO_SHOW)
        with pytest.raises(payments.PaymentError, match="perdoadas"):
            payments.waive_charge(fine.charge.pk, board_member, "Sem motivo")

    def test_paid_charge_cannot_be_waived(
        self, charge: Charge, player: Player, board_member: Player, receipt_upload
    ) -> None:
        receipt = payments.submit_receipt(charge.pk, player, receipt_upload())
        with pytest.raises(payments.PaymentError, match="em análise"):
            payments.waive_charge(charge.pk, board_member, "Engano")
        payments.approve_receipt(receipt.pk, board_member)
        with pytest.raises(payments.PaymentError, match="paga"):
            payments.waive_charge(charge.pk, board_member, "Engano")


class TestExpenses:
    def test_create_update_delete(self, board_member: Player, clock: FixedClock) -> None:
        expense = payments.save_expense(
            board_member, amount=Decimal("600.00"), description=" Aluguel ", day=TODAY
        )
        assert expense.description == "Aluguel"
        assert expense.created_by == board_member
        payments.save_expense(
            board_member,
            amount=Decimal("650.00"),
            description="Aluguel da quadra",
            day=TODAY,
            expense=expense,
        )
        expense.refresh_from_db()
        assert expense.amount == Decimal("650.00")
        payments.delete_expense(expense.pk, board_member)
        assert not Expense.objects.exists()

    def test_amount_must_be_positive(self, board_member: Player, clock: FixedClock) -> None:
        with pytest.raises(ValidationError):
            payments.save_expense(
                board_member, amount=Decimal("0.00"), description="Nada", day=TODAY
            )

    def test_only_the_board(self, player: Player, clock: FixedClock) -> None:
        with pytest.raises(PermissionDenied):
            payments.save_expense(player, amount=Decimal("1.00"), description="x", day=TODAY)


class TestReports:
    def test_month_totals_by_reference_period(
        self,
        charge: Charge,
        player: Player,
        make_player,
        board_member: Player,
        receipt_upload,
        clock: FixedClock,
    ) -> None:
        receipt = payments.submit_receipt(charge.pk, player, receipt_upload())
        payments.approve_receipt(receipt.pk, board_member)
        make_player("Didi", player_type=PlayerType.MENSALISTA)
        charges.ensure_monthly_charges()
        payments.save_expense(
            board_member, amount=Decimal("45.50"), description="Bolas", day=date(2026, 10, 2)
        )
        payments.save_expense(
            board_member, amount=Decimal("999.00"), description="Outro mês", day=date(2026, 9, 30)
        )

        report = month_report(date(2026, 10, 20), TODAY, with_people=True)
        assert report.month == date(2026, 10, 1)
        assert report.collected == Decimal("30.00")
        assert report.expenses_total == Decimal("45.50")
        assert report.balance == Decimal("-15.50")
        assert report.pending_total == Decimal("100.00")
        assert [row.charge.player.name for row in report.paid] == ["Garrincha"]
        assert [row.charge.player.name for row in report.pending] == ["Didi"]

        public = month_report(date(2026, 10, 1), TODAY, with_people=False)
        assert public.collected == report.collected
        assert public.paid == public.pending == public.expenses == ()

    def test_collected_is_split_by_origin(
        self,
        charge: Charge,
        player: Player,
        game_day: GameDay,
        make_player,
        board_member: Player,
        receipt_upload,
    ) -> None:
        mensalista = make_player("Didi", player_type=PlayerType.MENSALISTA)
        charges.ensure_monthly_charges()
        monthly = Charge.objects.get(player=mensalista, kind=ChargeKind.MONTHLY)
        fine = fines.record_fine(player, game_day, FineReason.NO_SHOW)
        fine_charge = Charge.objects.get(fine=fine)
        unpaid_monthly = monthly.amount
        for owed in (charge, monthly, fine_charge):
            receipt = payments.submit_receipt(owed.pk, owed.player, receipt_upload())
            payments.approve_receipt(receipt.pk, board_member)
        # Only confirmed payments count: a pending avulso charge adds nothing.
        other = make_player("Pendente")
        Charge.objects.create(
            player=other,
            kind=ChargeKind.GAME,
            game_day=game_day,
            amount=Decimal("30.00"),
            reference_date=game_day.date,
            due_date=game_day.date,
        )

        report = month_report(game_day.date, TODAY, with_people=False)
        assert report.collected_walk_in == charge.amount
        assert report.collected_monthly == unpaid_monthly
        assert report.collected_fines == fine_charge.amount
        assert report.collected == (
            report.collected_walk_in + report.collected_monthly + report.collected_fines
        )

    def test_pending_list_and_late_mensalistas(
        self, charge: Charge, make_player, clock: FixedClock
    ) -> None:
        late = make_player("Atrasado", player_type=PlayerType.MENSALISTA)
        Charge.objects.create(
            player=late,
            kind=ChargeKind.MONTHLY,
            amount=Decimal("100.00"),
            reference_date=date(2026, 9, 1),
            due_date=date(2026, 9, 10),
        )
        rows = open_charges(TODAY)
        assert [row.charge.player.name for row in rows] == ["Atrasado", "Garrincha"]
        assert rows[0].overdue
        assert not rows[1].overdue
        assert [row.charge.player for row in late_mensalistas(TODAY)] == [late]
