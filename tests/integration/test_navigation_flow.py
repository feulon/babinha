"""Where a screen returns to: the `?next=` it was opened with when it is a local address, else a
fixed parent. A foreign address is never followed."""

from urllib.parse import quote

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core import navigation
from core.clock import FixedClock
from finance import services as payments
from finance.models import Charge, ChargeKind, Receipt
from games import services as games
from tests.conftest import PNG_BYTES, end_game

pytestmark = pytest.mark.django_db

HOME = reverse("games:home")


def client_for(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


@pytest.fixture
def board_client(board_member: Player, clock: FixedClock) -> Client:
    return client_for(board_member)


@pytest.fixture
def player_client(make_player, clock: FixedClock) -> Client:
    return client_for(make_player("Zico"))


class TestOneHome:
    def test_already_signed_in_keeps_the_requested_page(self, board_client: Client) -> None:
        target = reverse("board:pending")
        response = board_client.get(reverse("accounts:login"), {"next": target})
        assert response["Location"] == target
        assert board_client.get(reverse("accounts:login"))["Location"] == HOME
        assert board_client.get(reverse("accounts:signup"))["Location"] == HOME

    def test_already_signed_in_ignores_a_foreign_next(self, player_client: Client) -> None:
        response = player_client.get(reverse("accounts:login"), {"next": "//evil.example/"})
        assert response["Location"] == HOME


class TestNext:
    def test_only_local_addresses_count(self, rf, board_member: Player) -> None:
        for raw, expected in (
            ("/ranking/", "/ranking/"),
            ("//evil.example/", None),
            ("https://evil.example/", None),
            ("javascript:alert(1)", None),
            ("", None),
            ("/mais/", None),  # the screen itself: a back link to it would loop
        ):
            request = rf.get("/mais/", {"next": raw})
            assert navigation.requested_next(request) == expected, raw

    def test_a_long_chain_is_dropped(self, rf) -> None:
        request = rf.get("/" + "a" * navigation.MAX_NEXT_LENGTH)
        assert navigation.with_next(request, "/jogador/1/") == "/jogador/1/"

    def test_a_next_is_added_to_an_address_with_a_query(self, rf) -> None:
        request = rf.get("/ranking/?ordem=gols")
        assert navigation.with_next(request, "/jogador/1/?aba=origem") == (
            "/jogador/1/?aba=origem&next=%2Franking%2F%3Fordem%3Dgols"
        )


class TestEditAndReceiptBack:
    @pytest.fixture
    def charge(self, clock: FixedClock, club_values, make_player, president: Player) -> Charge:
        player = make_player("Garrincha", referred_by=president)
        game_day = games.current_game_day()
        games.join_list(game_day.pk, player)
        end_game(game_day)
        return Charge.objects.get(player=player, kind=ChargeKind.GAME)

    @pytest.fixture
    def receipt(self, charge: Charge) -> Receipt:
        return payments.submit_receipt(
            charge.pk, charge.player, SimpleUploadedFile("pix.png", PNG_BYTES)
        )

    def test_approving_keeps_the_way_back_and_so_does_undoing(
        self, board_client: Client, receipt: Receipt
    ) -> None:
        pending = reverse("board:pending")
        approve = reverse("board:approve_receipt", args=[receipt.pk])
        response = board_client.post(approve, {"next": pending})
        review = reverse("board:receipt", args=[receipt.pk])
        assert response["Location"] == f"{review}?next=%2Fdiretoria%2Fpendencias%2F"
        undo = board_client.post(
            reverse("board:undo_approval", args=[receipt.pk]), {"next": pending}
        )
        assert undo["Location"] == f"{review}?next=%2Fdiretoria%2Fpendencias%2F"

    def test_refusing_returns_to_where_the_review_started(
        self, board_client: Client, receipt: Receipt
    ) -> None:
        pending = reverse("board:pending")
        response = board_client.post(
            reverse("board:refuse_receipt", args=[receipt.pk]),
            {"reason": "Valor errado", "next": pending},
        )
        assert response["Location"] == pending

    def test_editing_a_player_saves_back_to_where_it_started(
        self, board_client: Client, charge: Charge
    ) -> None:
        player = charge.player
        edit = reverse("board:player_edit", args=[player.pk])
        pending = reverse("board:pending")
        form = {
            "name": player.name,
            "nickname": player.nickname,
            "phone": player.phone,
            "email": player.email or "",
            "player_type": player.player_type,
            "position": player.position,
            "pote": player.pote,
            "is_active": "on",
            "referred_by": player.referred_by_id,
        }
        response = board_client.post(f"{edit}?next={quote(pending, safe='')}", form)
        assert response.status_code == 302, response.content.decode()
        assert response["Location"] == pending

    def test_the_new_password_keeps_the_way_back(
        self, board_client: Client, charge: Charge
    ) -> None:
        edit = reverse("board:player_edit", args=[charge.player_id])
        action = reverse("board:reset_player_password", args=[charge.player_id])
        response = board_client.post(f"{action}?next=%2Fdiretoria%2Fpendencias%2F")
        assert response["Location"] == f"{edit}?next=%2Fdiretoria%2Fpendencias%2F"
