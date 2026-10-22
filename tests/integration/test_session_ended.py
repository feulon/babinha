"""A session that ended mid-action, and the pages a signed-in player's browser may not keep."""

from collections.abc import Callable
from urllib.parse import urlencode

import pytest
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from tests.conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db

SCREEN = "/pagamentos/"
REFERER = {"HTTP_REFERER": f"http://testserver{SCREEN}"}

# Every action a plain form can send that the login page used to send back as a bare GET.
POST_ONLY = [
    pytest.param("finance:join_monthly", {}, id="mensalista"),
    pytest.param("games:join", {}, id="lista-entrar"),
    pytest.param("finance:upload_receipt", {"charge_id": 1}, id="comprovante"),
    pytest.param("board:approve_receipt", {"receipt_id": 1}, id="aprovar"),
]


def login_url(**query: str) -> str:
    return f"{reverse('accounts:login')}?{urlencode(query)}" if query else reverse("accounts:login")


class TestActionAfterTheSessionEnded:
    @pytest.mark.parametrize(("name", "kwargs"), POST_ONLY)
    def test_the_login_returns_to_the_screen_the_form_was_on(
        self, client: Client, name: str, kwargs: dict[str, object]
    ) -> None:
        response = client.post(reverse(name, kwargs=kwargs), **REFERER)
        assert response.status_code == 302
        assert response["Location"] == login_url(next=SCREEN)

    def test_the_query_of_the_screen_is_kept(self, client: Client) -> None:
        response = client.post(
            reverse("finance:join_monthly"),
            HTTP_REFERER="http://testserver/diretoria/jogadores/?q=ana",
        )
        assert response["Location"] == login_url(next="/diretoria/jogadores/?q=ana")

    def test_without_a_referer_it_is_the_plain_login_page(self, client: Client) -> None:
        response = client.post(reverse("finance:join_monthly"))
        assert response["Location"] == login_url()

    @pytest.mark.parametrize(
        "referer",
        ["https://evil.example/pagamentos/", "http://testserver/entrar/?next=/mensalista/"],
        ids=["another-host", "login-page"],
    )
    def test_a_referer_that_is_no_screen_to_return_to_is_ignored(
        self, client: Client, referer: str
    ) -> None:
        response = client.post(reverse("finance:join_monthly"), HTTP_REFERER=referer)
        assert response["Location"] == login_url()

    def test_a_get_keeps_its_own_address_as_the_place_to_return_to(self, client: Client) -> None:
        response = client.get(SCREEN, HTTP_REFERER="http://testserver/mais/")
        assert response["Location"] == f"{login_url()}?next={SCREEN}"

    def test_after_signing_in_the_player_is_on_the_screen_not_on_a_405(
        self, client: Client, make_player: Callable[..., Player]
    ) -> None:
        player = make_player("Maria")
        client.force_login(player)
        assert client.get(SCREEN).status_code == 200
        Session.objects.all().delete()

        sent = client.post(reverse("finance:join_monthly"), **REFERER)
        signed_in = client.post(
            sent["Location"], {"login": player.phone, "password": TEST_PASSWORD, "next": SCREEN}
        )
        assert signed_in.status_code == 302
        assert signed_in["Location"] == SCREEN
        assert client.get(signed_in["Location"]).status_code == 200


class TestSignedInPagesAreNotKept:
    @pytest.mark.parametrize("name", ["games:home", "board:dashboard", "finance:payments"])
    def test_a_signed_in_page_is_private_and_not_stored(
        self, board_member: Player, name: str
    ) -> None:
        client = Client()
        client.force_login(board_member)
        response = client.get(reverse(name), follow=True)
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"

    def test_the_profile_with_phone_and_email_is_private_and_not_stored(
        self, board_member: Player
    ) -> None:
        client = Client()
        client.force_login(board_member)
        response = client.get(reverse("community:player", args=[board_member.pk]))
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"

    def test_back_after_sair_meets_the_login_again(self, board_member: Player) -> None:
        client = Client()
        client.force_login(board_member)
        page = reverse("community:player", args=[board_member.pk])
        assert client.get(page).status_code == 200

        client.post(reverse("accounts:logout"))

        response = client.get(page)
        assert response.status_code == 302
        assert response["Location"] == f"{login_url()}?next={page}"

    def test_sair_ends_at_the_plain_login_page_whatever_the_referer(
        self, board_member: Player
    ) -> None:
        client = Client()
        client.force_login(board_member)
        response = client.post(reverse("accounts:logout"), **REFERER)
        assert response["Location"] == login_url()

    def test_the_login_page_is_left_to_the_browser(self, client: Client) -> None:
        response = client.get(reverse("accounts:login"))
        assert response.status_code == 200
        assert "no-store" not in response.get("Cache-Control", "")

    def test_what_is_not_a_page_is_left_to_the_browser(self, board_member: Player) -> None:
        client = Client()
        client.force_login(board_member)
        response = client.get(reverse("manifest"))
        assert "no-store" not in response.get("Cache-Control", "")
