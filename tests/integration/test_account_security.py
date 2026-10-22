"""Closed sign-up, changing one's own password, handing out logins, and the lockout message."""

from collections.abc import Callable
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from accounts import photos
from accounts.models import Player
from accounts.passwords import credentials_message
from core.clock import FixedClock
from tests.conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db

SIGNUP = reverse("accounts:signup")
LOGIN = reverse("accounts:login")
CHANGE = reverse("accounts:password_change")
CODE = "Baba2026"


def page(response) -> str:
    return response.content.decode()


def signup_data(referrer: Player, **overrides: str) -> dict:
    data = {
        "invite_code": CODE,
        "name": "Zico",
        "phone": "(21) 98888-7777",
        "email": "zico@example.com",
        "referrer_name": referrer.name,
        "password": "gol1",
        "password_confirm": "gol1",
        "accept_rules": "on",
    }
    return {**data, **overrides}


def signed_in(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


@pytest.fixture
def invite(settings) -> str:
    settings.BABA_INVITE_CODE = CODE
    return CODE


class TestInviteCode:
    def test_without_a_code_configured_sign_up_stays_open(
        self, client: Client, president: Player
    ) -> None:
        assert "Código de convite" not in page(client.get(SIGNUP))
        data = signup_data(president)
        del data["invite_code"]
        assert client.post(SIGNUP, data).status_code == 302

    def test_the_right_code_creates_the_account(
        self, client: Client, president: Player, invite
    ) -> None:
        assert client.post(SIGNUP, signup_data(president)).status_code == 302
        assert Player.objects.filter(phone="21988887777").exists()

    def test_the_code_ignores_case_and_surrounding_spaces(
        self, client: Client, president: Player, invite
    ) -> None:
        response = client.post(SIGNUP, signup_data(president, invite_code=f"  {CODE.lower()} "))
        assert response.status_code == 302

    @pytest.mark.parametrize("typed", ["", "errado", "Baba202"])
    def test_a_missing_or_wrong_code_creates_nothing(
        self, client: Client, president: Player, invite, typed: str
    ) -> None:
        response = client.post(SIGNUP, signup_data(president, invite_code=typed))
        assert response.status_code == 200
        assert not Player.objects.filter(phone="21988887777").exists()

    def test_a_wrong_code_is_explained(self, client: Client, president: Player, invite) -> None:
        response = client.post(SIGNUP, signup_data(president, invite_code="errado"))
        assert "Código de convite incorreto" in page(response)

    def test_a_wrong_code_does_not_reveal_who_plays(
        self, client: Client, president: Player, invite
    ) -> None:
        response = client.post(
            SIGNUP, signup_data(president, invite_code="errado", referrer_name="Ninguém Aqui")
        )
        assert "Código de convite incorreto" in page(response)
        assert "Não achamos ninguém" not in page(response)

    def test_a_wrong_code_does_not_reveal_which_phones_and_emails_have_an_account(
        self, client: Client, president: Player, invite
    ) -> None:
        data = signup_data(
            president, invite_code="errado", phone=president.phone, email=president.email
        )
        html = page(client.post(SIGNUP, data))
        assert "Código de convite incorreto" in html
        assert "Já existe uma conta" not in html

    def test_a_wrong_code_leaves_every_other_field_unchecked(
        self, client: Client, president: Player, invite, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unreachable(upload: object) -> bytes:
            raise AssertionError("the photo must not be opened with a wrong invite code")

        monkeypatch.setattr(photos, "process", unreachable)
        photo = SimpleUploadedFile("eu.jpg", b"not an image", content_type="image/jpeg")
        data = signup_data(
            president, invite_code="errado", password="1234", password_confirm="4321", photo=photo
        )
        response = client.post(SIGNUP, data)
        assert response.context["form"].errors == {
            "invite_code": ["Código de convite incorreto. Confira com quem convidou você."]
        }

    def test_the_right_code_still_reports_a_taken_phone(
        self, client: Client, president: Player, invite
    ) -> None:
        html = page(client.post(SIGNUP, signup_data(president, phone=president.phone)))
        assert "Já existe uma conta com este celular" in html

    def test_guessing_the_code_is_limited_per_client(
        self, client: Client, president: Player, invite, clock: FixedClock
    ) -> None:
        for _ in range(10):
            client.post(SIGNUP, signup_data(president, invite_code="chute"), REMOTE_ADDR="9.9.9.9")
        # Even the right code is refused now, and the answer does not say it was right.
        response = client.post(SIGNUP, signup_data(president), REMOTE_ADDR="9.9.9.9")
        assert response.status_code == 200
        assert "Muitos cadastros deste aparelho" in page(response)
        assert "Código de convite incorreto" not in page(response)
        assert not Player.objects.filter(phone="21988887777").exists()
        # Another client is not affected, and the block lifts with time.
        other = Client().post(SIGNUP, signup_data(president), REMOTE_ADDR="8.8.8.8")
        assert other.status_code == 302
        clock.advance(timedelta(hours=1, minutes=1))
        later = Client().post(
            SIGNUP,
            signup_data(president, phone="21977776666", email="outro@example.com"),
            REMOTE_ADDR="9.9.9.9",
        )
        assert later.status_code == 302


class TestChangePassword:
    def test_it_asks_for_login(self, client: Client) -> None:
        response = client.get(CHANGE)
        assert response.status_code == 302
        assert LOGIN in response["Location"]

    def change(self, client: Client, old: str, new: str, repeat: str | None = None):
        return client.post(
            CHANGE,
            {"old_password": old, "new_password1": new, "new_password2": repeat or new},
        )

    def test_the_new_password_replaces_the_old_one_and_keeps_the_session(
        self, make_player: Callable[..., Player]
    ) -> None:
        player = make_player("Jogador")
        client = signed_in(player)
        response = self.change(client, TEST_PASSWORD, "nov1")
        assert response.status_code == 302
        player.refresh_from_db()
        assert player.check_password("nov1")
        assert client.get(reverse("games:home")).status_code == 200  # still signed in
        Client().post(LOGIN, {"login": player.phone, "password": TEST_PASSWORD})
        assert Client().post(LOGIN, {"login": player.phone, "password": "nov1"}).status_code == 302

    def test_the_current_password_must_be_right(self, make_player: Callable[..., Player]) -> None:
        player = make_player("Jogador")
        response = self.change(signed_in(player), "xxxx", "nov1")
        assert response.status_code == 200
        player.refresh_from_db()
        assert player.check_password(TEST_PASSWORD)

    @pytest.mark.parametrize("new", ["abc", "abcde"])
    def test_the_new_password_has_four_characters(
        self, make_player: Callable[..., Player], new: str
    ) -> None:
        player = make_player("Jogador")
        response = self.change(signed_in(player), TEST_PASSWORD, new)
        assert "exatamente 4 caracteres" in page(response)
        player.refresh_from_db()
        assert player.check_password(TEST_PASSWORD)

    def test_the_repeat_must_match(self, make_player: Callable[..., Player]) -> None:
        player = make_player("Jogador")
        response = self.change(signed_in(player), TEST_PASSWORD, "nov1", repeat="nov2")
        assert response.status_code == 200
        player.refresh_from_db()
        assert player.check_password(TEST_PASSWORD)

    def test_guessing_the_current_password_is_limited(
        self, make_player: Callable[..., Player], clock: FixedClock
    ) -> None:
        player = make_player("Jogador")
        client = signed_in(player)
        for _ in range(5):
            self.change(client, "xxxx", "nov1")
        response = self.change(client, TEST_PASSWORD, "nov1")  # right now, refused anyway
        assert "Muitas tentativas erradas" in page(response)
        player.refresh_from_db()
        assert player.check_password(TEST_PASSWORD)
        clock.advance(timedelta(minutes=15))
        assert self.change(client, TEST_PASSWORD, "nov1").status_code == 302


class TestProvisionalPassword:
    def test_a_regular_password_shows_no_invitation(
        self, make_player: Callable[..., Player]
    ) -> None:
        html = page(signed_in(make_player("Jogador")).get(reverse("games:home")))
        assert "data-provisional-password" not in html

    def test_a_provisional_password_invites_the_player_to_change_it(
        self, make_player: Callable[..., Player]
    ) -> None:
        player = make_player("Jogador", password_is_provisional=True)
        client = signed_in(player)
        html = page(client.get(reverse("games:home")))
        assert "data-provisional-password" in html
        assert f'href="{CHANGE}"' in html
        assert "data-provisional-password" not in page(client.get(CHANGE))  # not nagging there

    def test_changing_it_clears_the_invitation(self, make_player: Callable[..., Player]) -> None:
        player = make_player("Jogador", password_is_provisional=True)
        client = signed_in(player)
        client.post(
            CHANGE,
            {"old_password": TEST_PASSWORD, "new_password1": "novo", "new_password2": "novo"},
        )
        player.refresh_from_db()
        assert not player.password_is_provisional
        assert "data-provisional-password" not in page(client.get(reverse("games:home")))

    def test_the_board_resetting_a_password_marks_it_provisional(
        self, make_player: Callable[..., Player], board_member: Player
    ) -> None:
        player = make_player("Jogador")
        response = signed_in(board_member).post(
            reverse("board:reset_player_password", kwargs={"player_id": player.pk})
        )
        assert response.status_code == 302
        player.refresh_from_db()
        assert player.password_is_provisional

    def test_choosing_a_password_from_the_recovery_link_clears_it(
        self, client: Client, make_player: Callable[..., Player]
    ) -> None:
        player = make_player("Jogador", password_is_provisional=True)
        client.post(reverse("accounts:password_reset"), {"email": player.email})
        link = next(
            line for line in mail.outbox[0].body.splitlines() if "/senha/redefinir/" in line
        )
        path = urlparse(link.strip()).path
        redirect_to_form = client.get(path)
        form_path = redirect_to_form["Location"]
        client.post(form_path, {"new_password1": "novo", "new_password2": "novo"})
        player.refresh_from_db()
        assert player.check_password("novo")
        assert not player.password_is_provisional


class TestHandedOutCredentials:
    def test_a_login_created_by_the_board_is_provisional_and_shows_copy_and_whatsapp(
        self, board_member: Player, clock: FixedClock
    ) -> None:
        client = signed_in(board_member)
        response = client.post(
            reverse("board:player_new"),
            {
                "name": "Novato",
                "phone": "(21) 98888-7777",
                "email": "zico@example.com",
                "player_type": "avulso",
                "position": "linha",
                "pote": 2,
            },
            follow=True,
        )
        player = Player.objects.get(phone="21988887777")
        assert player.password_is_provisional
        html = page(response)
        assert "data-copy=" in html
        assert "Copiar login e senha" in html
        assert "https://wa.me/5521988887777?text=" in html

    def test_the_message_names_the_login_the_password_and_where_to_change_it(self) -> None:
        message = credentials_message("21988887777", "ab3k")
        assert message == (
            "Seu login no Babinha: celular (21) 98888-7777, senha ab3k. "
            "Troque a senha em Mais > Alterar senha."
        )

    def test_the_whatsapp_link_carries_the_message(
        self, board_member: Player, make_player: Callable[..., Player]
    ) -> None:
        maria = make_player("Maria", phone="21977776666", referred_by=board_member)
        client = signed_in(board_member)
        client.post(reverse("board:reset_player_password", kwargs={"player_id": maria.pk}))
        html = page(client.get(reverse("board:player_edit", kwargs={"player_id": maria.pk})))
        link = next(part for part in html.split('"') if part.startswith("https://wa.me/"))
        link = link.replace("&amp;", "&")
        query = parse_qs(urlparse(link).query)
        assert link.startswith("https://wa.me/5521977776666?text=")
        assert query["text"][0].startswith("Seu login no Babinha: celular (21) 97777-6666, senha ")
        assert query["text"][0].endswith("Troque a senha em Mais > Alterar senha.")


class TestLockoutMessage:
    def test_it_says_until_when_and_what_to_do(
        self, client: Client, make_player: Callable[..., Player], clock: FixedClock
    ) -> None:
        player = make_player("Jogador")
        for _ in range(5):
            response = client.post(
                LOGIN, {"login": player.phone, "password": "xxxx"}, REMOTE_ADDR="7.7.7.7"
            )
        html = page(response)
        assert "Muitas tentativas erradas" in html
        assert "a partir das 09h15" in html  # the test clock is 09:00, the block lasts 15 minutes
        assert "peça uma nova senha à diretoria" in html

    def test_a_locked_account_says_no_device_gets_in_and_who_releases_it(
        self, client: Client, make_player: Callable[..., Player], clock: FixedClock
    ) -> None:
        player = make_player("Jogador")
        for n in range(20):
            response = Client().post(
                LOGIN, {"login": player.phone, "password": "xxxx"}, REMOTE_ADDR=f"7.7.7.{n // 4}"
            )
        html = page(response)
        assert "bloqueado em qualquer aparelho" in html
        assert "a partir das 10h00" in html  # the test clock is 09:00, the account block lasts 1h
        assert "peça à diretoria uma nova senha" in html

    def test_choosing_a_password_from_the_recovery_link_releases_a_locked_account(
        self, client: Client, make_player: Callable[..., Player], clock: FixedClock
    ) -> None:
        player = make_player("Jogador")
        for n in range(20):
            Client().post(
                LOGIN, {"login": player.phone, "password": "xxxx"}, REMOTE_ADDR=f"7.7.7.{n // 4}"
            )
        path = recovery_form_path(client, player)
        client.post(path, {"new_password1": "4321", "new_password2": "4321"})
        response = Client().post(
            LOGIN, {"login": player.phone, "password": "4321"}, REMOTE_ADDR="7.7.7.0"
        )
        assert response.status_code == 302

    def test_the_owner_on_another_network_still_gets_in(
        self, client: Client, make_player: Callable[..., Player], clock: FixedClock
    ) -> None:
        player = make_player("Jogador")
        for _ in range(5):
            client.post(LOGIN, {"login": player.phone, "password": "xxxx"}, REMOTE_ADDR="7.7.7.7")
        owner = Client().post(
            LOGIN, {"login": player.phone, "password": TEST_PASSWORD}, REMOTE_ADDR="1.1.1.1"
        )
        assert owner.status_code == 302


JOKE = "1234, tá de sacanagem?"


def recovery_form_path(client: Client, player: Player) -> str:
    client.post(reverse("accounts:password_reset"), {"email": player.email})
    link = next(line for line in mail.outbox[0].body.splitlines() if "/senha/redefinir/" in line)
    return client.get(urlparse(link.strip()).path)["Location"]


class TestJokePassword:
    """Choosing exactly 1234 is refused by the three forms that set a password."""

    def test_signing_up_with_1234_is_refused(self, client: Client, president: Player) -> None:
        response = client.post(
            SIGNUP, signup_data(president, password="1234", password_confirm="1234")
        )
        assert response.status_code == 200
        assert response.context["form"].errors["password"] == [JOKE]
        assert not Player.objects.filter(email="zico@example.com").exists()

    def test_signing_up_with_another_password_works(
        self, client: Client, president: Player
    ) -> None:
        response = client.post(SIGNUP, signup_data(president), follow=True)
        assert JOKE not in page(response)
        assert Player.objects.filter(email="zico@example.com").exists()

    def test_changing_to_1234_is_refused(self, make_player: Callable[..., Player]) -> None:
        player = make_player("Jogador")
        client = signed_in(player)
        response = client.post(
            CHANGE,
            {"old_password": TEST_PASSWORD, "new_password1": "1234", "new_password2": "1234"},
        )
        assert response.status_code == 200
        assert response.context["form"].errors["new_password1"] == [JOKE]
        player.refresh_from_db()
        assert player.check_password(TEST_PASSWORD)

    def test_changing_to_another_password_works(self, make_player: Callable[..., Player]) -> None:
        player = make_player("Jogador")
        client = signed_in(player)
        client.post(
            CHANGE,
            {"old_password": TEST_PASSWORD, "new_password1": "1235", "new_password2": "1235"},
        )
        player.refresh_from_db()
        assert player.check_password("1235")

    def test_the_recovery_link_refuses_1234(
        self, client: Client, make_player: Callable[..., Player]
    ) -> None:
        player = make_player("Jogador")
        form_path = recovery_form_path(client, player)
        response = client.post(form_path, {"new_password1": "1234", "new_password2": "1234"})
        assert response.status_code == 200
        assert response.context["form"].errors["new_password1"] == [JOKE]
        player.refresh_from_db()
        assert player.check_password(TEST_PASSWORD)

    def test_the_recovery_link_accepts_another_password(
        self, client: Client, make_player: Callable[..., Player]
    ) -> None:
        player = make_player("Jogador")
        form_path = recovery_form_path(client, player)
        client.post(form_path, {"new_password1": "4321", "new_password2": "4321"})
        player.refresh_from_db()
        assert player.check_password("4321")

    def test_an_existing_1234_account_still_signs_in(
        self, client: Client, make_player: Callable[..., Player]
    ) -> None:
        player = make_player("Jogador")
        player.set_password("1234")
        player.save()
        response = client.post(LOGIN, {"login": player.phone, "password": "1234"})
        assert response.status_code == 302
