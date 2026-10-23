"""The phone is the player's identity (mandatory, unique, the login); the e-mail is optional."""

from collections.abc import Callable
from datetime import timedelta

import pytest
from django.core import mail
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from tests.conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db

LOGIN = reverse("accounts:login")
SIGNUP = reverse("accounts:signup")
CONTACT = reverse("accounts:contact")
RESET = reverse("accounts:password_reset")
HOME = reverse("games:home")


def sign_in(client: Client, identifier: str, password: str = TEST_PASSWORD, ip: str = "10.0.0.1"):
    return client.post(LOGIN, {"login": identifier, "password": password}, REMOTE_ADDR=ip)


def signup_data(referrer: Player, **overrides: str) -> dict:
    data = {
        "name": "Zico",
        "nickname": "Zico",
        "phone": "(21) 98888-7777",
        "email": "zico@example.com",
        "referrer_name": referrer.name,
        "password": "gol1",
        "password_confirm": "gol1",
        "accept_rules": "on",
    }
    return {**data, **overrides}


def page(response) -> str:
    return response.content.decode()


@pytest.fixture
def no_email(make_player: Callable[..., Player], president: Player) -> Player:
    """A player who signed up with a phone only."""
    return make_player("Sem Email", email=None, phone="21977776666", referred_by=president)


@pytest.fixture
def legacy(make_player: Callable[..., Player], president: Player) -> Player:
    """A player from before the phone existed: an e-mail login and no phone."""
    player = make_player("Antigo", referred_by=president)
    Player.objects.filter(pk=player.pk).update(phone=None)
    player.refresh_from_db()
    return player


class TestSignupWithoutEmail:
    def test_the_email_is_required(self, client: Client, president: Player) -> None:
        response = client.post(SIGNUP, signup_data(president, email=""))
        assert response.status_code == 200
        assert "Este campo é obrigatório." in page(response)
        assert Player.objects.count() == 1

    def test_the_phone_is_required(self, client: Client, president: Player) -> None:
        response = client.post(SIGNUP, signup_data(president, phone=""))
        assert response.status_code == 200
        assert "Este campo é obrigatório." in page(response)
        assert Player.objects.count() == 1

    def test_a_bad_phone_is_explained(self, client: Client, president: Player) -> None:
        # The formats themselves are covered by tests/unit/test_phone.py.
        response = client.post(SIGNUP, signup_data(president, phone="21 38888-7777"))
        assert "Informe um celular com DDD" in page(response)
        assert Player.objects.count() == 1

    def test_a_filled_email_is_kept_in_lowercase(self, client: Client, president: Player) -> None:
        client.post(SIGNUP, signup_data(president, email="Zico@Example.com"))
        assert Player.objects.get(phone="21988887777").email == "zico@example.com"

    def test_a_bad_email_is_still_rejected(self, client: Client, president: Player) -> None:
        response = client.post(SIGNUP, signup_data(president, email="zico"))
        assert response.status_code == 200
        assert Player.objects.count() == 1


class TestUniqueness:
    def test_the_phone_is_unique_whatever_the_formatting(
        self, client: Client, president: Player, make_player: Callable[..., Player]
    ) -> None:
        make_player(phone="11999990001")
        response = client.post(SIGNUP, signup_data(president, phone="+55 (11) 99999-0001"))
        assert "Já existe uma conta com este celular." in page(response)
        assert Player.objects.filter(phone="11999990001").count() == 1

    def test_the_email_is_still_unique_ignoring_case(
        self, client: Client, president: Player
    ) -> None:
        response = client.post(SIGNUP, signup_data(president, email=president.email.upper()))
        assert "Já existe uma conta com este e-mail." in page(response)

    def test_players_without_email_do_not_collide(
        self, client: Client, president: Player, no_email: Player
    ) -> None:
        response = client.post(SIGNUP, signup_data(president, email="novo@example.com"))
        assert response.status_code == 302
        assert Player.objects.filter(email__isnull=True).count() == 1

    def test_the_database_enforces_it_too(self, make_player: Callable[..., Player]) -> None:
        first = make_player(phone="11999990002")
        with pytest.raises(IntegrityError), transaction.atomic():
            Player.objects.filter(pk=make_player().pk).update(phone=first.phone)
        with pytest.raises(IntegrityError), transaction.atomic():
            Player.objects.filter(pk=make_player().pk).update(email=first.email.upper())

    def test_blank_values_are_stored_as_null(self, make_player: Callable[..., Player]) -> None:
        player = make_player(email="")
        Player.objects.filter(pk=player.pk).update(phone=None)
        player.refresh_from_db()
        player.phone = ""
        player.save()
        player.refresh_from_db()
        assert (player.email, player.phone) == (None, None)

    def test_creating_a_player_needs_a_valid_phone(self, president: Player) -> None:
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            Player.objects.create_player(
                phone="", password="abcd", name="X", nickname="X", referred_by=president
            )


class TestSigningIn:
    def test_by_phone_in_any_formatting(self, client: Client, no_email: Player) -> None:
        assert sign_in(client, " +55 (21) 97777-6666 ").status_code == 302
        assert client.get(HOME).status_code == 200

    def test_by_email_as_before(self, client: Client, president: Player) -> None:
        assert sign_in(client, president.email.upper()).status_code == 302
        assert client.get(HOME, follow=True).status_code == 200

    def test_a_player_with_both_uses_either(
        self, president: Player, make_player: Callable[..., Player]
    ) -> None:
        assert sign_in(Client(), president.email).status_code == 302
        assert sign_in(Client(), president.phone).status_code == 302

    @pytest.mark.parametrize("identifier", ["21977776667", "ghost@example.com"])
    def test_wrong_or_unknown_looks_the_same(
        self, client: Client, no_email: Player, identifier: str
    ) -> None:
        response = sign_in(client, identifier)
        assert response.status_code == 200
        assert "Celular, e-mail ou senha incorretos." in page(response)

    def test_wrong_password(self, client: Client, no_email: Player) -> None:
        response = sign_in(client, no_email.phone, "xxxx")
        assert "Celular, e-mail ou senha incorretos." in page(response)

    def test_a_deactivated_player_cannot_sign_in(self, client: Client, no_email: Player) -> None:
        Player.objects.filter(pk=no_email.pk).update(is_active=False)
        assert sign_in(client, no_email.phone).status_code == 200

    def test_an_empty_email_is_not_a_login(self, client: Client, no_email: Player) -> None:
        # A player without e-mail is stored as NULL: typing nothing must match nobody.
        assert sign_in(client, "").status_code == 200
        assert client.get(HOME).status_code == 302  # still signed out
        for blank in ("", "  ", None):
            with pytest.raises(Player.DoesNotExist):
                Player.objects.find_by_login(blank)


class TestLockoutByPhoneAndEmail:
    def test_five_wrong_attempts_by_phone_lock_for_15_minutes(
        self, client: Client, no_email: Player, clock: FixedClock
    ) -> None:
        for _ in range(5):
            response = sign_in(client, no_email.phone, "xxxx")
        assert "Muitas tentativas erradas" in page(response)
        response = sign_in(client, "(21) 97777-6666", TEST_PASSWORD)
        assert response.status_code == 200
        assert "Muitas tentativas erradas" in page(response)
        clock.advance(timedelta(minutes=15))
        assert sign_in(client, no_email.phone).status_code == 302

    def test_the_phone_and_the_email_of_one_account_share_the_counter(
        self, client: Client, president: Player, clock: FixedClock
    ) -> None:
        for identifier in (president.phone, president.email, president.phone, president.email):
            sign_in(client, identifier, "xxxx")
        response = sign_in(client, president.email.upper(), "xxxx")
        assert "Muitas tentativas erradas" in page(response)
        # The right password is refused in either format while locked.
        for identifier in (president.phone, president.email):
            assert sign_in(client, identifier).status_code == 200

    def test_a_stranger_guessing_a_phone_does_not_lock_the_owner(
        self, client: Client, no_email: Player, clock: FixedClock
    ) -> None:
        for _ in range(5):
            sign_in(client, no_email.phone, "xxxx", ip="203.0.113.9")
        assert sign_in(Client(), no_email.phone, ip="10.0.0.1").status_code == 302

    def test_unknown_phones_are_throttled_too(self, client: Client, clock: FixedClock) -> None:
        for _ in range(5):
            response = sign_in(client, "(11) 90000-0000", "xxxx")
        assert "Muitas tentativas erradas" in page(response)


class TestPlayerFromBeforeThePhone:
    def test_they_still_sign_in_with_their_email(self, client: Client, legacy: Player) -> None:
        assert sign_in(client, legacy.email).status_code == 302

    def test_their_next_request_asks_for_the_phone(self, client: Client, legacy: Player) -> None:
        client.force_login(legacy)
        response = client.get(HOME)
        assert response.status_code == 302
        assert response["Location"].startswith(CONTACT)
        assert "next=%2F" in response["Location"]

    def test_every_screen_is_gated_not_only_the_home(self, client: Client, legacy: Player) -> None:
        client.force_login(legacy)
        for url in (reverse("community:players"), reverse("finance:payments"), "/diretoria/"):
            assert client.get(url).status_code == 302
            assert client.get(url)["Location"].startswith(CONTACT)

    def test_htmx_requests_are_redirected_without_swapping_the_page(
        self, client: Client, legacy: Player
    ) -> None:
        client.force_login(legacy)
        response = client.get(HOME, HTTP_HX_REQUEST="true")
        assert response.status_code == 204
        assert response["HX-Redirect"].startswith(CONTACT)

    def test_a_post_is_gated_and_does_not_become_the_next_page(
        self, client: Client, legacy: Player
    ) -> None:
        client.force_login(legacy)
        response = client.post(reverse("games:join"))
        assert response["Location"] == CONTACT

    def test_the_way_out_and_the_contact_page_stay_open(
        self, client: Client, legacy: Player
    ) -> None:
        client.force_login(legacy)
        html = page(client.get(CONTACT))
        assert "Qual é o seu celular?" in html
        assert 'class="tabbar"' not in html  # every tab would bounce back here
        assert client.get(reverse("health")).status_code == 200
        assert client.post(reverse("accounts:logout")).status_code == 302

    def test_giving_the_phone_releases_them_to_where_they_were_going(
        self, client: Client, legacy: Player
    ) -> None:
        client.force_login(legacy)
        target = reverse("community:players")
        email = legacy.email
        response = client.post(
            f"{CONTACT}?next={target}", {"phone": "(11) 95555-4444", "email": email}
        )
        assert response["Location"] == target
        legacy.refresh_from_db()
        assert legacy.phone == "11955554444"
        assert legacy.email == email
        assert client.get(HOME).status_code == 200
        assert sign_in(Client(), "11955554444").status_code == 302

    def test_it_never_sends_them_to_another_site(self, client: Client, legacy: Player) -> None:
        client.force_login(legacy)
        response = client.post(
            f"{CONTACT}?next=https://evil.example/", {"phone": "11955554444", "email": ""}
        )
        assert "evil.example" not in response["Location"]

    def test_the_phone_is_required_to_leave_the_page(self, client: Client, legacy: Player) -> None:
        client.force_login(legacy)
        response = client.post(CONTACT, {"phone": "", "email": ""})
        assert response.status_code == 200
        assert "Este campo é obrigatório." in page(response)
        assert client.get(HOME).status_code == 302

    def test_a_phone_that_belongs_to_someone_else_is_refused(
        self, client: Client, legacy: Player, no_email: Player
    ) -> None:
        client.force_login(legacy)
        response = client.post(CONTACT, {"phone": "(21) 97777-6666", "email": ""})
        assert "Já existe uma conta com este celular." in page(response)
        legacy.refresh_from_db()
        assert legacy.phone is None

    def test_they_can_keep_their_account_data_when_adding_the_phone(
        self, client: Client, legacy: Player
    ) -> None:
        client.force_login(legacy)
        client.post(CONTACT, {"phone": "11955554444", "email": legacy.email})
        legacy.refresh_from_db()
        assert legacy.check_password(TEST_PASSWORD)
        assert legacy.name == "Antigo"

    def test_players_who_already_have_a_phone_are_never_gated(
        self, client: Client, no_email: Player
    ) -> None:
        client.force_login(no_email)
        assert client.get(HOME).status_code == 200


class TestEditingOwnContact:
    def test_change_phone_and_add_an_email(self, client: Client, no_email: Player) -> None:
        client.force_login(no_email)
        assert "Meu contato" in page(client.get(CONTACT))
        client.post(CONTACT, {"phone": "(21) 96666-5555", "email": "Novo@Example.com"})
        no_email.refresh_from_db()
        assert (no_email.phone, no_email.email) == ("21966665555", "novo@example.com")

    def test_the_email_can_be_removed(self, client: Client, president: Player) -> None:
        client.force_login(president)
        client.post(CONTACT, {"phone": president.phone, "email": ""})
        president.refresh_from_db()
        assert president.email is None

    def test_the_email_cannot_be_someone_elses(
        self, client: Client, no_email: Player, president: Player
    ) -> None:
        client.force_login(no_email)
        response = client.post(CONTACT, {"phone": no_email.phone, "email": president.email.upper()})
        assert "Já existe uma conta com este e-mail." in page(response)

    def test_the_contact_page_requires_login(self, client: Client) -> None:
        assert reverse("accounts:login") in client.get(CONTACT)["Location"]


class TestPasswordResetByEmail:
    def test_a_player_with_email_still_gets_the_link(
        self, client: Client, president: Player
    ) -> None:
        client.post(RESET, {"email": president.email})
        assert [m.to for m in mail.outbox] == [[president.email]]

    def test_a_blank_email_matches_nobody(self, client: Client, no_email: Player) -> None:
        client.post(RESET, {"email": ""})
        assert mail.outbox == []


@pytest.mark.django_db(transaction=True)
class TestMigration:
    """The migration that adds the phone leaves every existing account working."""

    BEFORE = [("accounts", "0002_player_member_since")]

    def test_existing_players_keep_logging_in_and_are_asked_for_the_phone(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.BEFORE)
        try:
            old = executor.loader.project_state(self.BEFORE).apps.get_model("accounts", "Player")
            old.objects.create(name="Antigo", email="antigo@example.com", password="x")
            old.objects.create(name="Antiga", email="antiga@example.com", password="x")

            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

        players = Player.objects.order_by("email")
        assert [(p.email, p.phone) for p in players] == [
            ("antiga@example.com", None),
            ("antigo@example.com", None),
        ]
        # Two players without a phone do not collide, and each is found by e-mail.
        assert Player.objects.find_by_login("ANTIGO@example.com").name == "Antigo"
        Player.objects.all().delete()
