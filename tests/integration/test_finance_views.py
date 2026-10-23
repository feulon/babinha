"""Payment, receipt and finance screens, for players and the diretoria."""

from datetime import date
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from accounts.models import Player, PlayerType
from core.clock import FixedClock
from core.models import ClubSettings
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
from games import services
from tests.conftest import PDF_BYTES, PIX_HOLDER, PIX_KEY, PNG_BYTES, end_game

pytestmark = pytest.mark.django_db

HOME = reverse("games:home")
PAYMENTS = reverse("finance:payments")
REPORT = reverse("finance:report")


@pytest.fixture
def player(make_player, president: Player) -> Player:
    return make_player("Garrincha", referred_by=president)


@pytest.fixture
def player_client(client: Client, player: Player) -> Client:
    client.force_login(player)
    return client


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def president_client(president: Player) -> Client:
    client = Client()
    client.force_login(president)
    return client


@pytest.fixture
def charge(clock: FixedClock, club_values: ClubSettings, player: Player) -> Charge:
    game_day = services.current_game_day()
    services.join_list(game_day.pk, player)
    end_game(game_day)
    return Charge.objects.get(player=player, kind=ChargeKind.GAME)


def upload(client: Client, charge: Charge, content: bytes = PNG_BYTES, name: str = "pix.png"):
    url = reverse("finance:upload_receipt", kwargs={"charge_id": charge.pk})
    return client.post(url, {"file": SimpleUploadedFile(name, content), "next": HOME}, follow=True)


class TestPlayerHome:
    def test_home_shows_one_line_with_what_is_owed_and_when(
        self, player_client: Client, charge: Charge
    ) -> None:
        html = player_client.get(HOME).content.decode()
        card = html.split('data-state="pagamento"', 1)[1].split("</section>", 1)[0]
        assert "Pagamento pendente" in card
        assert "R$ 30,00</strong> a pagar" in card
        assert f"vence {charge.due_date:%d/%m}" in card
        assert f'<a href="{PAYMENTS}?next=' in card
        assert PIX_KEY not in html
        assert 'enctype="multipart/form-data"' not in html

    def test_payments_shows_the_pix_key_and_the_upload(
        self, player_client: Client, charge: Charge
    ) -> None:
        html = player_client.get(PAYMENTS).content.decode()
        assert "Pagamento pendente" in html
        assert "R$ 30,00" in html
        assert PIX_KEY in html
        assert f"Em nome de {PIX_HOLDER}" in html
        assert 'enctype="multipart/form-data"' in html
        assert "Domingo 18/10" in html

    def test_nothing_owed_shows_no_payment_block(
        self, player_client: Client, clock: FixedClock, club_values: ClubSettings
    ) -> None:
        assert 'data-state="pagamento"' not in player_client.get(HOME).content.decode()

    def test_missing_pix_key_is_explained(
        self, player_client: Client, charge: Charge, club_values: ClubSettings
    ) -> None:
        club_values.pix_key = ""
        club_values.save()
        html = player_client.get(PAYMENTS).content.decode()
        assert "ainda não cadastrou a chave Pix" in html

    def test_mensalista_sees_the_monthly_fee(
        self, client: Client, make_player, clock: FixedClock, club_values: ClubSettings
    ) -> None:
        client.force_login(make_player(player_type=PlayerType.MENSALISTA))
        assert "R$ 100,00</strong> a pagar" in client.get(HOME).content.decode()
        html = client.get(PAYMENTS).content.decode()
        assert "Mensalidade 10/2026" in html
        assert "R$ 100,00" in html


class TestUpload:
    def test_upload_puts_the_payment_under_review(
        self, player_client: Client, charge: Charge
    ) -> None:
        response = upload(player_client, charge)
        html = response.content.decode()
        assert response.redirect_chain[-1][0] == HOME
        assert "Comprovante enviado!" in html
        assert "Comprovante em análise" in html
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.UNDER_REVIEW

    def test_invalid_file_shows_an_error(self, player_client: Client, charge: Charge) -> None:
        html = upload(player_client, charge, b"GIF89a....", "pix.gif").content.decode()
        assert "Envie uma foto (JPG, PNG ou WebP) ou um PDF" in html
        assert not Receipt.objects.exists()

    def test_missing_file_shows_an_error(self, player_client: Client, charge: Charge) -> None:
        url = reverse("finance:upload_receipt", kwargs={"charge_id": charge.pk})
        html = player_client.post(url, {"next": HOME}, follow=True).content.decode()
        assert "Escolha a foto ou o PDF do comprovante." in html

    def test_someone_elses_charge_is_404(self, client: Client, make_player, charge: Charge) -> None:
        client.force_login(make_player())
        url = reverse("finance:upload_receipt", kwargs={"charge_id": charge.pk})
        response = client.post(url, {"file": SimpleUploadedFile("pix.png", PNG_BYTES)})
        assert response.status_code == 404

    def test_unknown_charge_is_404(self, player_client: Client, charge: Charge) -> None:
        response = player_client.post(
            reverse("finance:upload_receipt", kwargs={"charge_id": charge.pk + 1000}),
            {"file": SimpleUploadedFile("pix.png", PNG_BYTES, content_type="image/png")},
        )
        assert response.status_code == 404

    def test_external_next_is_ignored(self, player_client: Client, charge: Charge) -> None:
        url = reverse("finance:upload_receipt", kwargs={"charge_id": charge.pk})
        response = player_client.post(
            url,
            {"file": SimpleUploadedFile("pix.png", PNG_BYTES), "next": "https://evil.example"},
        )
        assert response["Location"] == PAYMENTS

    def test_refused_receipt_shows_reason_and_allows_resend(
        self, player_client: Client, charge: Charge, board_member: Player
    ) -> None:
        upload(player_client, charge)
        payments.refuse_receipt(Receipt.objects.get().pk, board_member, "Valor errado")
        html = player_client.get(PAYMENTS).content.decode()
        assert "Comprovante recusado: Valor errado. Envie outro." in html
        assert "Enviar comprovante" in html
        upload(player_client, charge)
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.UNDER_REVIEW


class TestReceiptFile:
    @pytest.fixture
    def receipt(self, charge: Charge, player: Player) -> Receipt:
        return payments.submit_receipt(charge.pk, player, SimpleUploadedFile("pix.pdf", PDF_BYTES))

    def url(self, receipt: Receipt) -> str:
        return reverse("finance:receipt_file", kwargs={"receipt_id": receipt.pk})

    def test_owner_sees_it_privately(self, player_client: Client, receipt: Receipt) -> None:
        response = player_client.get(self.url(receipt))
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == PDF_BYTES
        assert response["Content-Type"] == "application/pdf"
        assert response["Content-Disposition"].startswith("inline;")
        assert response["Cache-Control"] == "private, no-store"
        assert response["X-Content-Type-Options"] == "nosniff"

    def test_board_sees_it(self, board_client: Client, receipt: Receipt) -> None:
        assert board_client.get(self.url(receipt)).status_code == 200

    def test_other_players_get_404(self, client: Client, make_player, receipt: Receipt) -> None:
        client.force_login(make_player())
        assert client.get(self.url(receipt)).status_code == 404

    def test_anonymous_must_log_in(self, client: Client, receipt: Receipt) -> None:
        response = client.get(self.url(receipt))
        assert response.status_code == 302
        assert reverse("accounts:login") in response["Location"]


class TestReport:
    @pytest.fixture
    def paid(
        self, charge: Charge, player: Player, board_member: Player, president: Player
    ) -> Charge:
        receipt = payments.submit_receipt(
            charge.pk, player, SimpleUploadedFile("pix.png", PNG_BYTES)
        )
        payments.approve_receipt(receipt.pk, president)
        payments.save_expense(
            board_member, amount=Decimal("12.00"), description="Bomba de ar", day=date(2026, 10, 5)
        )
        return charge

    def test_players_see_only_totals(self, client: Client, make_player, paid: Charge) -> None:
        client.force_login(make_player("Zico"))
        html = client.get(REPORT).content.decode()
        assert 'data-total="arrecadado">R$ 30,00' in html
        assert 'data-total="avulsos">R$ 30,00' in html
        assert 'data-total="mensalistas">R$ 0,00' in html
        assert 'data-total="multas">R$ 0,00' in html
        assert 'data-total="despesas">R$ 12,00' in html
        assert 'data-total="saldo">R$ 18,00' in html
        assert "Garrincha" not in html
        assert "Bomba de ar" not in html
        assert "Pendentes" not in html

    def test_board_sees_who_paid_and_who_is_pending(
        self, board_client: Client, paid: Charge, make_player
    ) -> None:
        make_player("Didi", player_type=PlayerType.MENSALISTA)
        assert "Pagaram" not in board_client.get(REPORT).content.decode()
        html = board_client.get(reverse("board:cash")).content.decode()
        assert "Pagaram" in html
        assert "Garrincha" in html
        assert "Didi" in html  # monthly fee created on the board's visit
        assert "Bomba de ar" in html

    def test_hand_marked_payment_trail_is_for_the_board_only(
        self,
        board_client: Client,
        board_member: Player,
        charge: Charge,
        make_player,
        president: Player,
    ) -> None:
        payments.mark_paid(charge.pk, president, PaymentMethod.CASH, "na quadra")
        html = board_client.get(reverse("board:cash")).content.decode()
        assert f"Marcado como pago por {president.name}" in html
        assert "Dinheiro" in html
        assert "na quadra" in html
        other = Client()
        other.force_login(make_player("Zico"))
        assert "na quadra" not in other.get(REPORT).content.decode()

    def test_month_navigation(self, player_client: Client, paid: Charge) -> None:
        html = player_client.get(reverse("finance:report_month", args=["2026-09"])).content
        assert 'data-total="arrecadado">R$ 0,00' in html.decode()
        assert player_client.get("/caixa/2026-13/").status_code == 404


class TestBoardScreens:
    @pytest.fixture
    def receipt(self, charge: Charge, player: Player) -> Receipt:
        return payments.submit_receipt(charge.pk, player, SimpleUploadedFile("pix.png", PNG_BYTES))

    @pytest.mark.parametrize("name", ["board:receipts", "board:pending", "board:expenses"])
    def test_players_are_forbidden(self, player_client: Client, name: str, clock) -> None:
        assert player_client.get(reverse(name)).status_code == 403

    def test_dashboard_counts_receipts_and_late_mensalistas(
        self, board_client: Client, receipt: Receipt, make_player, clock: FixedClock
    ) -> None:
        late = make_player("Nilton", player_type=PlayerType.MENSALISTA)
        Charge.objects.create(
            player=late,
            kind=ChargeKind.MONTHLY,
            amount=Decimal("100.00"),
            reference_date=date(2026, 9, 1),
            due_date=date(2026, 9, 10),
        )
        html = board_client.get(reverse("board:dashboard")).content.decode()
        assert "1 para conferir" in html
        assert 'data-state="mensalidade-atrasada"' in html
        assert "Nilton" not in html
        assert "2 mensalidades em atraso" in html

    def test_review_queue_and_approve(self, president_client: Client, receipt: Receipt) -> None:
        queue = president_client.get(reverse("board:receipts")).content.decode()
        assert "Garrincha" in queue
        page = president_client.get(reverse("board:receipt", args=[receipt.pk])).content.decode()
        assert f'<img src="/comprovantes/{receipt.pk}/"' in page
        response = president_client.post(
            reverse("board:approve_receipt", args=[receipt.pk]), follow=True
        )
        assert "Pagamento de Garrincha confirmado." in response.content.decode()
        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.APPROVED

    def test_confirming_lands_on_the_receipt_where_it_can_be_undone(
        self, president_client: Client, receipt: Receipt
    ) -> None:
        response = president_client.post(
            reverse("board:approve_receipt", args=[receipt.pk]), follow=True
        )
        assert "Desfazer confirmação" in response.content.decode()
        response = president_client.post(
            reverse("board:undo_approval", args=[receipt.pk]), follow=True
        )
        assert "Confirmação de Garrincha desfeita." in response.content.decode()
        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.UNDER_REVIEW

    def test_mark_paid_is_one_tap_from_the_pending_list(
        self, president_client: Client, charge: Charge
    ) -> None:
        html = president_client.get(reverse("board:pending")).content.decode()
        url = reverse("board:mark_paid", args=[charge.pk])
        assert f'action="{url}"' in html
        assert president_client.get(url).status_code == 405
        response = president_client.post(
            url, {"next": reverse("board:pending") + "?x=1"}, follow=True
        )
        assert "Pagamento de Garrincha (R$ 30,00) registrado." in response.content.decode()
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.PAID
        assert charge.marked_paid_by is not None

    def test_mark_paid_ignores_foreign_redirects_and_refuses_non_board(
        self, president_client: Client, player_client: Client, charge: Charge
    ) -> None:
        url = reverse("board:mark_paid", args=[charge.pk])
        assert player_client.post(url).status_code == 403
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.PENDING
        response = president_client.post(url, {"next": "https://evil.example/"})
        assert response.url == reverse("board:pending")

    def test_refuse_requires_reason(self, board_client: Client, receipt: Receipt) -> None:
        url = reverse("board:refuse_receipt", args=[receipt.pk])
        response = board_client.post(url, {"reason": ""})
        assert response.status_code == 200
        assert "Este campo é obrigatório." in response.content.decode()
        response = board_client.post(url, {"reason": "Ilegível"}, follow=True)
        assert "Comprovante de Garrincha recusado." in response.content.decode()
        receipt.refresh_from_db()
        assert receipt.refusal_reason == "Ilegível"

    def test_pending_list_and_waive(self, board_client: Client, charge: Charge) -> None:
        html = board_client.get(reverse("board:pending")).content.decode()
        assert "Garrincha" in html
        assert "Dispensar" in html
        response = board_client.post(
            reverse("board:waive", args=[charge.pk]), {"reason": "Convidado"}, follow=True
        )
        assert "Cobrança de Garrincha dispensada." in response.content.decode()
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.WAIVED

    def test_the_filter_leaves_out_what_already_has_a_receipt(
        self, board_client: Client, player: Player, make_player, charge: Charge
    ) -> None:
        other = make_player("Didi")
        services.join_list(services.current_game_day().pk, other)
        end_game(services.current_game_day())
        payments.submit_receipt(charge.pk, player, SimpleUploadedFile("pix.png", PNG_BYTES))
        pending = reverse("board:pending")
        everyone = board_client.get(pending).content.decode()
        unpaid = board_client.get(pending + "?filtro=sem-comprovante").content.decode()
        assert "Garrincha" in everyone
        assert "Didi" in everyone
        assert "Didi" in unpaid
        assert "Garrincha" not in unpaid.split("Cobranças em aberto", 1)[1]

    def test_expenses_crud(self, board_client: Client, clock: FixedClock) -> None:
        response = board_client.post(
            reverse("board:expenses"),
            {"amount": "650,00", "description": "Aluguel da quadra", "date": "2026-10-04"},
            follow=True,
        )
        assert "Despesa lançada." in response.content.decode()
        expense = Expense.objects.get()
        assert expense.amount == Decimal("650.00")
        assert response.redirect_chain[-1][0] == reverse("board:expenses_month", args=["2026-10"])

        board_client.post(
            reverse("board:expense_edit", args=[expense.pk]),
            {"amount": "700,00", "description": "Aluguel da quadra", "date": "04/10/2026"},
        )
        expense.refresh_from_db()
        assert expense.amount == Decimal("700.00")

        board_client.post(reverse("board:expense_delete", args=[expense.pk]))
        assert not Expense.objects.exists()

    def test_expense_must_be_positive(self, board_client: Client, clock: FixedClock) -> None:
        response = board_client.post(
            reverse("board:expenses"), {"amount": "0", "description": "Nada", "date": "2026-10-04"}
        )
        assert response.status_code == 200
        assert "Informe um valor maior que zero." in response.content.decode()


class TestReplaceReceiptUnderReview:
    def test_under_review_shows_the_sent_file_and_a_way_to_send_another(
        self, player_client: Client, charge: Charge
    ) -> None:
        upload(player_client, charge)
        receipt = Receipt.objects.get()
        html = player_client.get(PAYMENTS).content.decode()
        section = html.split('data-charge="', 1)[1]
        assert "Em análise" in section
        file_url = reverse("finance:receipt_file", kwargs={"receipt_id": receipt.pk})
        assert f'href="{file_url}"' in section
        assert "Ver comprovante enviado" in section
        assert "Enviar outro" in section
        assert 'enctype="multipart/form-data"' in section

    def test_sending_another_replaces_the_first(
        self, player_client: Client, charge: Charge, board_member: Player
    ) -> None:
        upload(player_client, charge)
        first = Receipt.objects.get()
        html = upload(player_client, charge, PDF_BYTES, "outro.pdf").content.decode()
        assert "Comprovante enviado!" in html
        first.refresh_from_db()
        assert first.status == ReceiptStatus.SUPERSEDED
        waiting = Receipt.objects.get(status=ReceiptStatus.UNDER_REVIEW)
        assert waiting.original_name == "outro.pdf"
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.UNDER_REVIEW

    def test_the_diretoria_only_sees_the_new_one_in_the_queue(
        self, player_client: Client, board_client: Client, charge: Charge
    ) -> None:
        upload(player_client, charge)
        first = Receipt.objects.get()
        upload(player_client, charge)
        queue = board_client.get(reverse("board:receipts")).content.decode()
        assert queue.count("Garrincha") == 1
        old = board_client.get(reverse("board:receipt", args=[first.pk])).content.decode()
        assert "O jogador enviou outro comprovante no lugar deste." in old
        assert "Confirmar pagamento" not in old

    def test_a_paid_charge_offers_no_new_upload(
        self, player_client: Client, charge: Charge, board_member: Player, president: Player
    ) -> None:
        upload(player_client, charge)
        payments.approve_receipt(Receipt.objects.get().pk, president)
        assert "Enviar outro" not in player_client.get(PAYMENTS).content.decode()
        html = upload(player_client, charge).content.decode()
        assert "Esta cobrança já está paga." in html
