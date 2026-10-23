"""The player's payment history on the diretoria's player page: every charge and every receipt,
newest first, kept forever and visible only to the diretoria."""

import pytest
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from core.models import ClubSettings
from finance import services as payments
from finance.models import Charge, ChargeKind, ChargeStatus, Receipt, ReceiptStatus
from finance.selectors import payment_history
from fines import services as fines
from fines.models import FineReason
from games import services
from games.models import GameDay
from tests.conftest import PNG_BYTES, end_game

pytestmark = pytest.mark.django_db


@pytest.fixture
def player(make_player, president: Player) -> Player:
    return make_player("Garrincha", referred_by=president)


@pytest.fixture
def game_day(clock: FixedClock, club_values: ClubSettings) -> GameDay:
    return services.current_game_day()


@pytest.fixture
def charge(game_day: GameDay, player: Player) -> Charge:
    services.join_list(game_day.pk, player)
    end_game(game_day)
    return Charge.objects.get(player=player, kind=ChargeKind.GAME)


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


def png(name: str = "pix.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PNG_BYTES)


def page(client: Client, player: Player) -> str:
    response = client.get(reverse("board:player_edit", kwargs={"player_id": player.pk}))
    assert response.status_code == 200
    return response.content.decode()


def stored(receipt: Receipt) -> bool:
    return storages["receipts"].exists(receipt.file.name)


class TestHistory:
    def test_lists_every_charge_newest_first_with_reasons(
        self, charge: Charge, game_day: GameDay, player: Player
    ) -> None:
        late = fines.record_late_fine(player, game_day, 15)
        no_show = fines.record_fine(player, game_day, FineReason.NO_SHOW)
        reasons = [e.reason for e in payment_history(player)]
        assert set(reasons) == {"Jogo", "Multa de atraso", "Multa de falta"}
        assert len(reasons) == 3
        assert {late.charge.pk, no_show.charge.pk} <= {e.charge.pk for e in payment_history(player)}
        keys = [(e.charge.reference_date, e.charge.pk) for e in payment_history(player)]
        assert keys == sorted(keys, reverse=True)

    def test_other_players_charges_are_not_included(
        self, charge: Charge, make_player, player: Player
    ) -> None:
        assert payment_history(make_player()) == ()
        assert [e.charge.pk for e in payment_history(player)] == [charge.pk]

    def test_keeps_every_receipt_of_a_charge(
        self, charge: Charge, player: Player, board_member: Player, president: Player
    ) -> None:
        first = payments.submit_receipt(charge.pk, player, png())
        replacement = payments.submit_receipt(charge.pk, player, png())
        payments.refuse_receipt(replacement.pk, board_member, "Ilegível")
        third = payments.submit_receipt(charge.pk, player, png())
        payments.approve_receipt(third.pk, president)
        (entry,) = payment_history(player)
        assert entry.situation == "Aprovado"
        assert [r.pk for r in entry.receipts] == [third.pk, replacement.pk, first.pk]
        assert [r.status for r in entry.receipts] == [
            ReceiptStatus.APPROVED,
            ReceiptStatus.REFUSED,
            ReceiptStatus.SUPERSEDED,
        ]

    def test_charge_without_receipt_has_none(self, charge: Charge, player: Player) -> None:
        (entry,) = payment_history(player)
        assert entry.receipts == ()
        assert entry.situation == "Pendente"


class TestPage:
    def test_board_sees_receipts_links_reviewers_and_missing_proof(
        self, board_client: Client, charge: Charge, player: Player, board_member: Player
    ) -> None:
        html = page(board_client, player)
        assert "Histórico de pagamentos" in html
        assert "Sem comprovante" in html
        receipt = payments.submit_receipt(charge.pk, player, png())
        payments.refuse_receipt(receipt.pk, board_member, "Valor errado")
        html = page(board_client, player)
        file_url = reverse("finance:receipt_file", kwargs={"receipt_id": receipt.pk})
        assert file_url in html
        assert "Sem comprovante" not in html
        assert "Recusado" in html
        assert board_member.name in html
        assert "Valor errado" in html

    def test_players_cannot_open_the_page_or_other_receipts(
        self, client: Client, charge: Charge, player: Player, make_player
    ) -> None:
        receipt = payments.submit_receipt(charge.pk, player, png())
        client.force_login(make_player())
        url = reverse("board:player_edit", kwargs={"player_id": player.pk})
        assert client.get(url).status_code in (302, 403, 404)
        file_url = reverse("finance:receipt_file", kwargs={"receipt_id": receipt.pk})
        assert client.get(file_url).status_code == 404

    def test_anonymous_cannot_open_the_file(
        self, client: Client, charge: Charge, player: Player
    ) -> None:
        receipt = payments.submit_receipt(charge.pk, player, png())
        file_url = reverse("finance:receipt_file", kwargs={"receipt_id": receipt.pk})
        assert client.get(file_url).status_code == 302


class TestNothingIsDeleted:
    def test_files_and_rows_survive_replace_refuse_approve_and_undo(
        self, charge: Charge, player: Player, board_member: Player, president: Player
    ) -> None:
        first = payments.submit_receipt(charge.pk, player, png())
        second = payments.submit_receipt(charge.pk, player, png())
        payments.refuse_receipt(second.pk, board_member, "Valor errado")
        third = payments.submit_receipt(charge.pk, player, png())
        payments.approve_receipt(third.pk, president)
        payments.undo_approval(third.pk, president)
        payments.approve_receipt(third.pk, president)
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.PAID
        for receipt in (first, second, third):
            assert Receipt.objects.filter(pk=receipt.pk).exists()
            assert stored(receipt)
