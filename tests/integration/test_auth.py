import re
from collections.abc import Callable
from datetime import timedelta

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from accounts.models import Player, PlayerType, Position, ThrottleEvent
from baba import settings as production_settings
from core.clock import FixedClock
from tests.conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db

LOGIN = reverse("accounts:login")
SIGNUP = reverse("accounts:signup")
RESET = reverse("accounts:password_reset")


def login(client: Client, identifier: str, password: str, ip: str = "10.0.0.1"):
    """Sign in with whatever the player types in the single login field: phone or e-mail."""
    return client.post(LOGIN, {"login": identifier, "password": password}, REMOTE_ADDR=ip)


def test_production_hashes_passwords_with_argon2() -> None:
    assert production_settings.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")


class TestSignup:
    def form(self, referrer: Player, **overrides: str) -> dict:
        data = {
            "name": "Zico",
            "nickname": "Zico",
            "phone": "(21) 98888-7777",
            "email": "Zico@Example.com",
            "referrer_name": referrer.name,
            "password": "gol1",
            "password_confirm": "gol1",
            "accept_rules": "on",
        }
        return {**data, **overrides}

    def test_newcomer_defaults(self, client: Client, president: Player, clock) -> None:
        response = client.post(SIGNUP, self.form(president))
        assert response.status_code == 302
        player = Player.objects.get(phone="21988887777")
        assert player.email == "zico@example.com"
        assert player.referred_by is None
        assert player.referrer_text == president.name
        assert player.player_type == PlayerType.AVULSO
        assert player.position == Position.LINHA
        assert player.pote == 3
        assert not player.is_board
        assert player.check_password("gol1")
        # Signed in straight away.
        assert client.get(reverse("games:home")).status_code == 200

    def test_email_is_required(self, client: Client, president: Player) -> None:
        response = client.post(SIGNUP, self.form(president, email=""))
        assert response.status_code == 200
        assert "email" in response.context["form"].errors
        assert not Player.objects.filter(phone="21988887777").exists()

    def test_email_must_be_valid(self, client: Client, president: Player) -> None:
        response = client.post(SIGNUP, self.form(president, email="zico"))
        assert response.status_code == 200
        assert "email" in response.context["form"].errors

    def test_referrer_is_optional(self, client: Client, president: Player) -> None:
        response = client.post(SIGNUP, self.form(president, referrer_name=""))
        assert response.status_code == 302
        player = Player.objects.get(phone="21988887777")
        assert player.referred_by is None
        assert player.referrer_text == ""

    @pytest.mark.parametrize(
        "typed", ["Socrates BrasileiroS", "Ninguém", "  o   primo  do Zé ", "Saiu da lista"]
    )
    def test_the_referrer_is_free_text_that_never_blocks(
        self, client: Client, president: Player, typed: str
    ) -> None:
        response = client.post(SIGNUP, self.form(president, referrer_name=typed))
        assert response.status_code == 302
        player = Player.objects.get(phone="21988887777")
        assert player.referred_by is None
        assert player.referrer_text == " ".join(typed.split())

    def test_the_referrer_text_has_a_length_limit(self, client: Client, president: Player) -> None:
        response = client.post(SIGNUP, self.form(president, referrer_name="x" * 61))
        assert "referrer_name" in response.context["form"].errors

    def test_the_page_does_not_list_the_members(self, client: Client, president: Player) -> None:
        html = client.get(SIGNUP).content.decode()
        assert president.name not in html
        assert "<select" not in html

    @pytest.mark.parametrize("password", ["abc", "abcde"])
    def test_password_must_have_four_characters(
        self, client: Client, president: Player, password: str
    ) -> None:
        response = client.post(
            SIGNUP, self.form(president, password=password, password_confirm=password)
        )
        assert "exatamente 4 caracteres" in response.content.decode()
        assert not Player.objects.filter(phone="21988887777").exists()

    def test_email_is_unique_ignoring_case(self, client: Client, president: Player) -> None:
        response = client.post(SIGNUP, self.form(president, email=president.email.upper()))
        assert "Já existe uma conta" in response.content.decode()

    def test_signup_without_any_player_explains_bootstrap(self, client: Client) -> None:
        response = client.get(SIGNUP)
        assert "Ainda não há jogadores" in response.content.decode()


class TestBootstrapCommand:
    def test_creates_the_president_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from django.core.management import CommandError, call_command

        monkeypatch.setenv("BABA_BOOTSTRAP_PASSWORD", "abcd")
        call_command(
            "criar_presidente",
            "--telefone",
            "(11) 91234-5678",
            "--email",
            "Pres@Example.com",
            "--nome",
            "Pres",
        )
        president = Player.objects.get()
        assert president.phone == "11912345678"
        assert president.email == "pres@example.com"
        assert president.is_presidente
        assert president.is_board
        assert president.referred_by is None
        with pytest.raises(CommandError, match="Já existe"):
            call_command(
                "criar_presidente",
                "--telefone",
                "11912345679",
                "--email",
                "b@example.com",
                "--nome",
                "B",
            )

    def test_rejects_bad_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from django.core.management import CommandError, call_command

        monkeypatch.setenv("BABA_BOOTSTRAP_PASSWORD", "abcdef")
        with pytest.raises(CommandError, match="4 caracteres"):
            call_command(
                "criar_presidente",
                "--telefone",
                "11912345679",
                "--email",
                "a@example.com",
                "--nome",
                "A",
            )
        assert not Player.objects.exists()

    def test_rejects_the_joke_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from django.core.management import CommandError, call_command

        monkeypatch.setenv("BABA_BOOTSTRAP_PASSWORD", "1234")
        with pytest.raises(CommandError, match="1234, tá de sacanagem"):
            call_command(
                "criar_presidente",
                "--telefone",
                "11912345679",
                "--email",
                "a@example.com",
                "--nome",
                "A",
            )
        assert not Player.objects.exists()

    def test_the_email_is_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from django.core.management import CommandError, call_command

        monkeypatch.setenv("BABA_BOOTSTRAP_PASSWORD", "abcd")
        with pytest.raises(CommandError, match="--email"):
            call_command("criar_presidente", "--telefone", "11912345679", "--nome", "A")
        assert not Player.objects.exists()

    @pytest.mark.parametrize("email", ["sem-arroba", "a@", "", "  "])
    def test_the_email_must_look_like_one(
        self, monkeypatch: pytest.MonkeyPatch, email: str
    ) -> None:
        from django.core.management import CommandError, call_command

        monkeypatch.setenv("BABA_BOOTSTRAP_PASSWORD", "abcd")
        with pytest.raises(CommandError, match="e-mail válido"):
            call_command(
                "criar_presidente",
                "--telefone",
                "11912345679",
                "--email",
                email,
                "--nome",
                "A",
            )
        assert not Player.objects.exists()


class TestAlreadySignedIn:
    @pytest.mark.parametrize("url", [LOGIN, SIGNUP])
    def test_login_and_signup_send_a_signed_in_board_member_to_the_inicio_with_a_note(
        self, client: Client, president: Player, url: str
    ) -> None:
        client.force_login(president)
        response = client.get(url, follow=True)
        assert response.redirect_chain[-1][0] == reverse("games:home")
        assert "Você já está logado." in response.content.decode()


class TestLoginLockout:
    def test_login_is_case_insensitive(self, client: Client, president: Player, clock) -> None:
        response = login(client, president.email.upper(), TEST_PASSWORD)
        assert response.status_code == 302

    def test_five_wrong_attempts_lock_for_15_minutes(
        self, client: Client, president: Player, clock: FixedClock
    ) -> None:
        for _ in range(5):
            response = login(client, president.email, "xxxx")
        assert "Muitas tentativas erradas" in response.content.decode()

        # Even the right password is refused while locked.
        response = login(client, president.email, TEST_PASSWORD)
        assert response.status_code == 200
        assert "Muitas tentativas erradas" in response.content.decode()

        clock.advance(timedelta(minutes=15))
        assert login(client, president.email, TEST_PASSWORD).status_code == 302

    def test_lockout_does_not_affect_the_owner_on_another_device(
        self, client: Client, president: Player, clock: FixedClock
    ) -> None:
        for _ in range(5):
            login(client, president.email, "xxxx", ip="203.0.113.9")
        owner = Client()
        assert login(owner, president.email, TEST_PASSWORD, ip="10.0.0.1").status_code == 302

    def test_four_errors_then_success_resets_the_count(
        self, client: Client, president: Player, clock: FixedClock
    ) -> None:
        for _ in range(4):
            login(client, president.email, "xxxx")
        assert login(client, president.email, TEST_PASSWORD).status_code == 302
        client.logout()
        for _ in range(4):
            login(client, president.email, "xxxx")
        assert login(client, president.email, TEST_PASSWORD).status_code == 302

    def test_coarse_limit_per_client_across_accounts(
        self, client: Client, make_player: Callable[..., Player], clock: FixedClock
    ) -> None:
        target = make_player()
        for n in range(30):
            login(client, f"nobody{n}@example.com", "xxxx", ip="198.51.100.7")
        response = login(client, target.email, TEST_PASSWORD, ip="198.51.100.7")
        assert "Muitas tentativas erradas" in response.content.decode()
        assert login(Client(), target.email, TEST_PASSWORD, ip="10.9.9.9").status_code == 302

    def test_twenty_failures_lock_the_account_whatever_the_client(
        self, client: Client, president: Player, clock: FixedClock
    ) -> None:
        # Four per address stays under the per-client lockout: only the account limit counts them.
        for n in range(20):
            login(Client(), president.email, "xxxx", ip=f"198.51.100.{n // 4}")
        fresh = login(Client(), president.phone, TEST_PASSWORD, ip="192.0.2.1")
        assert fresh.status_code == 200
        assert "bloqueado em qualquer aparelho" in fresh.content.decode()
        clock.advance(timedelta(hours=1))
        assert login(Client(), president.phone, TEST_PASSWORD, ip="192.0.2.1").status_code == 302

    def test_the_account_limit_also_counts_unknown_logins(
        self, client: Client, president: Player, clock: FixedClock
    ) -> None:
        for n in range(20):
            login(Client(), "ninguem@example.com", "xxxx", ip=f"198.51.100.{n // 4}")
        html = login(Client(), "ninguem@example.com", "xxxx", ip="192.0.2.1").content.decode()
        assert "bloqueado em qualquer aparelho" in html

    def test_a_new_password_from_the_board_releases_a_locked_account(
        self, client: Client, president: Player, board_member: Player, make_player, clock
    ) -> None:
        player = make_player("Zico", referred_by=president)
        for n in range(20):
            login(Client(), player.phone, "xxxx", ip=f"198.51.100.{n // 4}")
        board = Client()
        board.force_login(board_member)
        board.post(reverse("board:reset_player_password", args=[player.pk]))
        new_password = board.get(reverse("board:player_edit", args=[player.pk])).context[
            "new_password"
        ]["password"]
        response = login(Client(), player.phone, new_password, ip="198.51.100.0")
        assert response.status_code == 302

    def test_ipv6_addresses_in_one_64_share_the_client_limits(
        self, client: Client, president: Player, clock: FixedClock
    ) -> None:
        for n in range(1, 6):
            login(Client(), president.email, "xxxx", ip=f"2001:db8:0:1::{n:x}")
        same_block = login(Client(), president.email, TEST_PASSWORD, ip="2001:db8:0:1::ff")
        assert "Muitas tentativas erradas" in same_block.content.decode()
        other_block = login(Client(), president.email, TEST_PASSWORD, ip="2001:db8:0:2::1")
        assert other_block.status_code == 302

    def test_old_events_are_pruned(
        self, client: Client, president: Player, clock: FixedClock
    ) -> None:
        login(client, president.email, "xxxx")
        clock.advance(timedelta(days=2))
        login(client, president.email, "xxxx")
        assert ThrottleEvent.objects.filter(scope="login:account-client").count() == 1


class TestPasswordReset:
    def request_link(self, client: Client, email: str) -> str:
        response = client.post(RESET, {"email": email})
        assert response.status_code == 302
        assert len(mail.outbox) == 1
        body = mail.outbox[0].body
        match = re.search(r"https?://[^\s]+/senha/redefinir/[^\s]+/", body)
        assert match, body
        return match.group(0).split("testserver", 1)[1]

    def test_reset_by_email_link_works_once(self, client: Client, president: Player) -> None:
        link = self.request_link(client, president.email)
        assert mail.outbox[0].to == [president.email]
        response = client.get(link, follow=True)
        form_url = response.redirect_chain[-1][0]
        response = client.post(form_url, {"new_password1": "nova", "new_password2": "nova"})
        assert response.status_code == 302
        president.refresh_from_db()
        assert president.check_password("nova")

        # Same link again: invalid.
        response = client.get(link, follow=True)
        assert "Link inválido" in response.content.decode()

    def test_reset_link_expires(
        self, client: Client, president: Player, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        link = self.request_link(client, president.email)
        settings.PASSWORD_RESET_TIMEOUT = -1
        response = client.get(link, follow=True)
        assert "Link inválido" in response.content.decode()

    def test_unknown_email_looks_the_same(self, client: Client, president: Player) -> None:
        response = client.post(RESET, {"email": "ghost@example.com"})
        assert response.status_code == 302
        assert mail.outbox == []

    def test_reset_emails_are_rate_limited_per_account(
        self, client: Client, president: Player, clock: FixedClock
    ) -> None:
        for _ in range(5):
            client.post(RESET, {"email": president.email})
        assert len(mail.outbox) == 3
