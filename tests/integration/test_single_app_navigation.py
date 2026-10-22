"""One app for everyone: everybody signs in to the Início, and the address of the former mode
switch only sends back to a screen of this site."""

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from tests.conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture
def board_client(board_member: Player, clock) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


class TestSignIn:
    @pytest.mark.parametrize("who", ["player", "board_member"])
    def test_everyone_signs_in_to_the_inicio(self, who: str, request, clock) -> None:
        if who == "player":
            person = request.getfixturevalue("make_player")("Comum")
        else:
            person = request.getfixturevalue("board_member")
        response = Client().post(
            reverse("accounts:login"), {"login": person.phone, "password": TEST_PASSWORD}
        )
        assert response["Location"] == reverse("games:home")


class TestFormerModeAddress:
    def test_it_returns_to_the_screen_it_was_pressed_on(self, board_client: Client) -> None:
        here = reverse("board:players") + "?q=ze"
        response = board_client.post("/modo/", {"mode": "jogador", "next": here})
        assert response["Location"] == here

    @pytest.mark.parametrize("here", ["", "//evil.example/x", "https://evil.example/x"])
    def test_without_a_screen_of_this_site_it_goes_to_the_inicio(
        self, board_client: Client, here: str
    ) -> None:
        response = board_client.post("/modo/", {"mode": "diretoria", "next": here})
        assert response["Location"] == reverse("games:home")
