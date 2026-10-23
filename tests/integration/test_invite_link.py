"""The invite link: sign-up opened with who invited and the group code already filled in."""

from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player

pytestmark = pytest.mark.django_db

SIGNUP = reverse("accounts:signup")
CODE = "bola-9"


def origin_page(client: Client, player: Player):
    return client.get(reverse("community:player", args=[player.pk]) + "?aba=origem")


@pytest.fixture
def zico(make_player: Callable[..., Player], president: Player) -> Player:
    return make_player("Zico da Silva")


class TestLinkGeneration:
    def test_the_link_opens_signup_with_the_inviter_and_the_code(
        self, client: Client, zico: Player, settings
    ) -> None:
        settings.BABA_INVITE_CODE = CODE
        client.force_login(zico)
        link = origin_page(client, zico).context["invite_link"]
        parts = urlsplit(link)
        assert parts.path == SIGNUP
        assert parts.netloc == "testserver"
        assert parse_qs(parts.query) == {"indicacao": ["Zico da Silva"], "codigo": [CODE]}

    def test_without_a_group_code_there_is_none_in_the_link(
        self, client: Client, zico: Player, settings
    ) -> None:
        settings.BABA_INVITE_CODE = ""
        client.force_login(zico)
        link = origin_page(client, zico).context["invite_link"]
        assert parse_qs(urlsplit(link).query) == {"indicacao": ["Zico da Silva"]}

    def test_other_players_do_not_get_a_link_on_someone_elses_profile(
        self, client: Client, zico: Player, president: Player
    ) -> None:
        client.force_login(president)
        response = origin_page(client, zico)
        assert "invite_link" not in response.context
        assert "data-share=" not in response.content.decode()


class TestSignupPrefill:
    def test_name_and_code_come_filled_from_the_link(
        self, client: Client, president: Player, settings
    ) -> None:
        settings.BABA_INVITE_CODE = CODE
        form = client.get(SIGNUP, {"indicacao": "Zico da Silva", "codigo": CODE}).context["form"]
        assert form["referrer_name"].value() == "Zico da Silva"
        assert form["invite_code"].value() == CODE

    def test_a_plain_signup_stays_empty(self, client: Client, president: Player, settings) -> None:
        settings.BABA_INVITE_CODE = CODE
        form = client.get(SIGNUP).context["form"]
        assert not form["referrer_name"].value()
        assert not form["invite_code"].value()

    def test_the_code_is_ignored_when_the_group_has_none(
        self, client: Client, president: Player, settings
    ) -> None:
        settings.BABA_INVITE_CODE = ""
        response = client.get(SIGNUP, {"indicacao": "Zico", "codigo": "x"})
        assert "invite_code" not in response.context["form"].fields
        assert response.context["form"]["referrer_name"].value() == "Zico"

    def test_a_signup_through_the_link_keeps_the_referrer_text(
        self, client: Client, president: Player, settings
    ) -> None:
        settings.BABA_INVITE_CODE = CODE
        data = {
            "invite_code": CODE,
            "name": "Falcao",
            "nickname": "Falcao",
            "phone": "(11) 98765-4321",
            "email": "falcao@example.com",
            "referrer_name": "Zico da Silva",
            "password": "gol1",
            "password_confirm": "gol1",
            "accept_rules": "on",
        }
        assert client.post(SIGNUP, data).status_code == 302
        assert Player.objects.get(name="Falcao").referrer_text == "Zico da Silva"

    def test_an_overlong_name_in_the_link_is_cut(self, client: Client, president: Player) -> None:
        form = client.get(SIGNUP, {"indicacao": "x" * 200}).context["form"]
        assert len(form["referrer_name"].value()) == 60
