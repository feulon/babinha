"""Sign-up keeps who referred the player as free text; the profile connects it to a player."""

from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player

pytestmark = pytest.mark.django_db

REFERRER = reverse("accounts:referrer")


def signed_in(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


@pytest.fixture
def newcomer(make_player: Callable[..., Player], president: Player) -> Player:
    player = make_player("Zico")
    Player.objects.filter(pk=player.pk).update(referrer_text="Presi")
    player.refresh_from_db()
    return player


class TestConnecting:
    def test_requires_a_signed_in_player(self, client: Client) -> None:
        assert client.get(REFERRER).status_code == 302
        assert "entrar" in client.get(REFERRER)["Location"]

    def test_search_lists_matching_active_players_without_me(
        self, newcomer: Player, president: Player, make_player: Callable[..., Player]
    ) -> None:
        gone = make_player("Presente Saiu")
        Player.objects.filter(pk=gone.pk).update(is_active=False)
        html = signed_in(newcomer).get(REFERRER, {"q": "pres"}).content.decode()
        assert "Presidente" in html
        assert "Presente Saiu" not in html
        assert "Zico</strong>" not in html

    def test_connects_the_chosen_player(self, newcomer: Player, president: Player) -> None:
        response = signed_in(newcomer).post(REFERRER, {"player": president.pk})
        assert response.status_code == 302
        newcomer.refresh_from_db()
        assert newcomer.referred_by == president
        assert newcomer.referrer_text == "Presi"  # what they typed is kept

    def test_the_link_wins_over_the_text_on_the_profile(
        self, newcomer: Player, president: Player
    ) -> None:
        client = signed_in(newcomer)
        url = reverse("community:player", args=[newcomer.pk]) + "?aba=origem"
        before = client.get(url).content.decode()
        assert "Presi" in before
        assert "Ligar a um jogador" in before
        client.post(REFERRER, {"player": president.pk})
        after = client.get(url).content.decode()
        assert "Presidente</a>" in after
        assert "texto livre" not in after

    def test_swaps_the_referrer(
        self, newcomer: Player, president: Player, make_player: Callable[..., Player]
    ) -> None:
        other = make_player("Outro")
        client = signed_in(newcomer)
        client.post(REFERRER, {"player": president.pk})
        client.post(REFERRER, {"player": other.pk})
        newcomer.refresh_from_db()
        assert newcomer.referred_by == other

    def test_removes_the_referral_and_the_text(self, newcomer: Player, president: Player) -> None:
        client = signed_in(newcomer)
        client.post(REFERRER, {"player": president.pk})
        client.post(REFERRER, {"remove": "1"})
        newcomer.refresh_from_db()
        assert newcomer.referred_by is None
        assert newcomer.referrer_text == ""

    def test_nobody_can_be_their_own_or_a_descendant_referrer(
        self, president: Player, make_player: Callable[..., Player]
    ) -> None:
        child = make_player("Filho", referred_by=president)
        grandchild = make_player("Neto", referred_by=child)
        client = signed_in(child)
        client.post(REFERRER, {"player": child.pk})
        client.post(REFERRER, {"player": grandchild.pk})
        child.refresh_from_db()
        assert child.referred_by == president

    def test_an_inactive_or_unknown_player_is_refused(
        self, newcomer: Player, make_player: Callable[..., Player]
    ) -> None:
        gone = make_player("Saiu")
        Player.objects.filter(pk=gone.pk).update(is_active=False)
        client = signed_in(newcomer)
        for value in (gone.pk, 99999, "abc", ""):
            client.post(REFERRER, {"player": value})
        newcomer.refresh_from_db()
        assert newcomer.referred_by is None

    def test_the_president_has_nothing_to_connect(self, president: Player) -> None:
        assert signed_in(president).get(REFERRER).status_code == 302

    def test_existing_links_are_untouched(
        self, president: Player, make_player: Callable[..., Player]
    ) -> None:
        linked = make_player("Ligado", referred_by=president)
        linked.refresh_from_db()
        assert linked.referred_by == president
        assert linked.referrer_text == ""
