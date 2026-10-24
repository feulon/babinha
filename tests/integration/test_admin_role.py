"""The hidden admin (the creator): the presidente's powers, but never shown as presidente, and
turned on only from the database or `definir_admin`. Also: only the presidente confirms payments."""

import inspect
from collections.abc import Callable
from io import StringIO

import pytest
from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import Client
from django.urls import reverse

import accounts.forms
import board.forms
from accounts.models import Player
from community.models import Season
from core.clock import FixedClock
from core.models import ClubSettings
from finance import services as payments
from finance.models import Charge, ChargeKind, ChargeStatus, PaymentMethod, Receipt, ReceiptStatus
from finance.selectors import pix_info
from games import services as games
from tests.conftest import PNG_BYTES, end_game
from tests.integration.test_board_player_management import edit_url, form_data
from tests.integration.test_money_forms import values

pytestmark = pytest.mark.django_db

SETTINGS = reverse("board:settings")
PENDING = reverse("board:pending")


@pytest.fixture
def admin_player(make_player: Callable[..., Player], president: Player) -> Player:
    return make_player("Criador", is_admin=True, referred_by=president)


def login(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


@pytest.fixture
def charge(clock: FixedClock, club_values: ClubSettings, make_player, president: Player) -> Charge:
    player = make_player("Garrincha", referred_by=president)
    game_day = games.current_game_day()
    games.join_list(game_day.pk, player)
    end_game(game_day)
    return Charge.objects.get(player=player, kind=ChargeKind.GAME)


@pytest.fixture
def receipt(charge: Charge) -> Receipt:
    return payments.submit_receipt(
        charge.pk, charge.player, SimpleUploadedFile("pix.png", PNG_BYTES)
    )


class TestPowers:
    def test_nobody_is_admin_by_default(self, make_player, president: Player) -> None:
        assert not make_player("Zico").is_admin
        assert not president.is_admin

    def test_the_admin_counts_as_diretoria_and_has_the_presidentes_powers(
        self, admin_player: Player
    ) -> None:
        assert admin_player.is_board
        assert admin_player.has_presidente_powers
        assert not admin_player.is_presidente
        assert not admin_player.is_diretoria

    def test_the_presidente_has_the_powers(self, president: Player) -> None:
        assert president.has_presidente_powers

    def test_the_rest_of_the_diretoria_and_players_do_not(
        self, board_member: Player, make_player
    ) -> None:
        assert board_member.is_board
        assert not board_member.has_presidente_powers
        assert not make_player("Zico").has_presidente_powers

    def test_an_inactive_admin_has_nothing(self, admin_player: Player) -> None:
        Player.objects.filter(pk=admin_player.pk).update(is_active=False)
        admin_player.refresh_from_db()
        assert not admin_player.is_board
        assert not admin_player.has_presidente_powers

    def test_the_database_still_allows_a_single_presidente(
        self, president: Player, admin_player: Player
    ) -> None:
        assert Player.objects.filter(is_presidente=True).count() == 1
        assert Player.objects.filter(is_admin=True).count() == 1


class TestConfirmingPayments:
    """Marking a charge as paid and approving a Pix receipt: presidente or admin, nobody else."""

    def test_the_services_refuse_the_rest_of_the_diretoria(
        self, board_member: Player, charge: Charge, receipt: Receipt
    ) -> None:
        with pytest.raises(PermissionDenied):
            payments.mark_paid(charge.pk, board_member, PaymentMethod.CASH)
        with pytest.raises(PermissionDenied):
            payments.approve_receipt(receipt.pk, board_member)
        charge.refresh_from_db()
        receipt.refresh_from_db()
        assert charge.status == ChargeStatus.UNDER_REVIEW
        assert receipt.status == ReceiptStatus.UNDER_REVIEW

    def test_the_diretoria_cannot_undo_a_confirmation_either(
        self, board_member: Player, president: Player, receipt: Receipt
    ) -> None:
        payments.approve_receipt(receipt.pk, president)
        with pytest.raises(PermissionDenied):
            payments.undo_approval(receipt.pk, board_member)

    def test_the_diretoria_still_refuses_and_waives(
        self, board_member: Player, charge: Charge, receipt: Receipt
    ) -> None:
        payments.refuse_receipt(receipt.pk, board_member, "Ilegível")
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.REFUSED
        payments.waive_charge(charge.pk, board_member, "Perdoado")

    def test_the_admin_marks_paid_and_approves(
        self, admin_player: Player, charge: Charge, receipt: Receipt
    ) -> None:
        payments.approve_receipt(receipt.pk, admin_player)
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.PAID
        payments.undo_approval(receipt.pk, admin_player)
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.UNDER_REVIEW

    def test_the_admin_marks_cash_payments(self, admin_player: Player, charge: Charge) -> None:
        payments.mark_paid(charge.pk, admin_player, PaymentMethod.CASH)
        charge.refresh_from_db()
        assert charge.marked_paid_by == admin_player

    def test_the_views_answer_403_to_the_rest_of_the_diretoria(
        self, board_member: Player, charge: Charge, receipt: Receipt
    ) -> None:
        client = login(board_member)
        for name, arg in (("board:mark_paid", charge.pk), ("board:approve_receipt", receipt.pk)):
            assert client.post(reverse(name, args=[arg])).status_code == 403
        assert client.post(reverse("board:undo_approval", args=[receipt.pk])).status_code == 403
        charge.refresh_from_db()
        assert charge.status == ChargeStatus.UNDER_REVIEW

    def test_the_views_work_for_the_admin(self, admin_player: Player, receipt: Receipt) -> None:
        client = login(admin_player)
        assert client.post(reverse("board:approve_receipt", args=[receipt.pk])).status_code == 302
        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.APPROVED

    def test_screens_offer_no_button_the_diretoria_cannot_use(
        self, board_member: Player, charge: Charge, receipt: Receipt, make_player, president
    ) -> None:
        other = make_player("Didi", referred_by=president)
        games.join_list(games.current_game_day().pk, other)
        client = login(board_member)
        pending = client.get(PENDING).content.decode()
        assert "Marcar como pago</button>" not in pending
        assert reverse("board:mark_paid", args=[charge.pk]) not in pending
        assert "Só o presidente marca uma cobrança como paga." in pending
        page = client.get(reverse("board:receipt", args=[receipt.pk])).content.decode()
        assert "Confirmar pagamento" not in page
        assert "Só o presidente confirma o pagamento." in page
        assert "Recusar comprovante" in page

    @pytest.mark.parametrize("who", ["president", "admin_player"])
    def test_screens_offer_the_buttons_to_whoever_can_use_them(
        self, who: str, request: pytest.FixtureRequest, charge: Charge
    ) -> None:
        client = login(request.getfixturevalue(who))
        assert "Marcar como pago</button>" in client.get(PENDING).content.decode()
        receipt = payments.submit_receipt(
            charge.pk, charge.player, SimpleUploadedFile("pix.png", PNG_BYTES)
        )
        page = client.get(reverse("board:receipt", args=[receipt.pk])).content.decode()
        assert "Confirmar pagamento" in page

    def test_the_undo_button_is_the_presidentes_too(
        self, board_member: Player, president: Player, receipt: Receipt
    ) -> None:
        payments.approve_receipt(receipt.pk, president)
        page = login(board_member).get(reverse("board:receipt", args=[receipt.pk])).content
        assert "Desfazer confirmação" not in page.decode()
        page = login(president).get(reverse("board:receipt", args=[receipt.pk])).content
        assert "Desfazer confirmação" in page.decode()


class TestPixKeyAndHolder:
    def test_the_admin_changes_the_key_and_the_holder(
        self, admin_player: Player, clock: FixedClock
    ) -> None:
        data = values(pix_key="admin@baba.com.br", pix_holder="Titular Novo")
        assert login(admin_player).post(SETTINGS, data).status_code == 302
        club = ClubSettings.load()
        assert (club.pix_key, club.pix_holder) == ("admin@baba.com.br", "Titular Novo")
        assert club.pix_key_changed_by == admin_player

    def test_the_admin_is_never_the_holder(
        self, admin_player: Player, president: Player, club_values: ClubSettings
    ) -> None:
        ClubSettings.objects.filter(pk=1).update(pix_holder="")
        assert pix_info().holder == ""
        assert admin_player.name not in pix_info().holder


class TestHidden:
    def test_the_admin_has_no_role_badge_in_the_players_list(
        self, board_member: Player, admin_player: Player, president: Player
    ) -> None:
        html = login(board_member).get(reverse("board:players")).content.decode()
        row = html.split("Criador", 1)[1].split("</li>", 1)[0]
        assert "badge badge--wait" not in row
        assert "Administrador" not in html
        assert 'badge badge--wait">Presidente' in html  # the real presidente still shows

    def test_the_admin_profile_shows_no_role(
        self, board_member: Player, admin_player: Player
    ) -> None:
        url = reverse("community:player", args=[admin_player.pk])
        html = login(board_member).get(url, {"aba": "resumo"}).content.decode()
        assert "Cargo" not in html
        assert "Administrador" not in html

    def test_the_admin_can_end_the_season_like_the_presidente(
        self, admin_player: Player, clock: FixedClock
    ) -> None:
        response = login(admin_player).post(reverse("community:end_season"))
        assert response.status_code == 302
        assert response.url == reverse("community:season")
        assert Season.objects.filter(ends_on__isnull=False, ended_by=admin_player).exists()

    def test_the_rest_of_the_diretoria_still_cannot_end_the_season(
        self, board_member: Player
    ) -> None:
        assert login(board_member).post(reverse("community:end_season")).status_code == 403

    def test_the_diretoria_cannot_reset_the_admins_password_or_switch_them_off(
        self, board_member: Player, admin_player: Player
    ) -> None:
        client = login(board_member)
        url = reverse("board:reset_player_password", args=[admin_player.pk])
        assert client.post(url).status_code == 403
        client.post(edit_url(admin_player), form_data(admin_player, is_active=""))
        admin_player.refresh_from_db()
        assert admin_player.is_active
        assert admin_player.password_is_provisional is False

    def test_the_admin_manages_the_presidente_like_the_presidente_does(
        self, admin_player: Player, president: Player
    ) -> None:
        url = reverse("board:reset_player_password", args=[president.pk])
        assert login(admin_player).post(url).status_code == 302


class TestNoPathSwitchesItOn:
    def test_no_form_of_the_app_has_the_field(self) -> None:
        for module in (accounts.forms, board.forms):
            for _, form in inspect.getmembers(module, inspect.isclass):
                if issubclass(form, forms.ModelForm) and form._meta.model is Player:
                    assert form._meta.fields not in (None, "__all__"), form.__name__
                    assert "is_admin" not in form._meta.fields, form.__name__

    def test_the_django_admin_does_not_manage_players(self) -> None:
        assert not admin.site.is_registered(Player)

    def test_editing_a_player_ignores_is_admin(
        self, board_member: Player, president: Player, make_player
    ) -> None:
        target = make_player("Zico", referred_by=president)
        for editor in (board_member, president):
            data = form_data(target, is_admin="on")
            assert login(editor).post(edit_url(target), data).status_code == 302
            target.refresh_from_db()
            assert not target.is_admin

    def test_creating_a_player_ignores_is_admin(self, president: Player) -> None:
        data = {
            "name": "Zé Novo",
            "nickname": "Zé Novo",
            "phone": "(11) 98765-4321",
            "email": "ze@example.com",
            "player_type": "avulso",
            "position": "linha",
            "pote": 2,
            "is_admin": "on",
        }
        assert login(president).post(reverse("board:player_new"), data).status_code == 302
        assert not Player.objects.get(name="Zé Novo").is_admin

    def test_signup_ignores_is_admin(self, client: Client, president: Player, clock) -> None:
        data = {
            "name": "Zico",
            "nickname": "Zico",
            "phone": "(21) 98888-7777",
            "email": "zico@example.com",
            "referrer_name": president.name,
            "password": "gol1",
            "password_confirm": "gol1",
            "accept_rules": "on",
            "is_admin": "on",
        }
        client.post(reverse("accounts:signup"), data)
        assert not Player.objects.get(phone="21988887777").is_admin

    def test_a_player_cannot_make_themselves_admin(self, make_player, president: Player) -> None:
        player = make_player("Zico", referred_by=president)
        client = login(player)
        client.post(
            reverse("accounts:profile"), {"name": "Zico", "nickname": "Zico", "is_admin": "on"}
        )
        client.post(
            reverse("accounts:contact"),
            {"phone": player.phone, "email": "z@example.com", "is_admin": "on"},
        )
        player.refresh_from_db()
        assert not player.is_admin


def run(*args: str) -> str:
    out = StringIO()
    call_command("definir_admin", *args, stdout=out)
    return out.getvalue()


class TestCommand:
    def test_turns_it_on_and_off_by_email(self, make_player, president: Player) -> None:
        player = make_player("Criador", email="feulon@gmail.com", referred_by=president)
        assert "ligado" in run("--email", "Feulon@Gmail.com")
        player.refresh_from_db()
        assert player.is_admin
        assert "desligado" in run("--email", "feulon@gmail.com", "--remover")
        player.refresh_from_db()
        assert not player.is_admin

    def test_it_is_idempotent(self, admin_player: Player) -> None:
        admin_player.email = "a@example.com"
        admin_player.save()
        assert "Nada mudou" in run("--email", "a@example.com")

    def test_it_does_not_create_accounts(self, president: Player) -> None:
        with pytest.raises(CommandError, match="Nenhuma conta"):
            run("--email", "ninguem@example.com")
        assert Player.objects.count() == 1

    def test_it_leaves_the_presidente_alone(self, president: Player) -> None:
        with pytest.raises(CommandError, match="presidente"):
            run("--email", president.email)
        president.refresh_from_db()
        assert president.is_presidente
        assert not president.is_admin
