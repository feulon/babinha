"""Teams that were confirmed and then stopped matching the list or the potes: the Painel and the
player say so instead of staying green, and confirming waits for an adjustment."""

import random
from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from board import tiles
from games import services as games
from matchday import services
from matchday.models import LineupSpot
from tests.conftest import Sunday

pytestmark = pytest.mark.django_db

DAY = "2026-10-18"


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday]) -> Sunday:
    return fill_sunday()


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def confirmed(sunday: Sunday, board_member: Player) -> Sunday:
    services.draw(sunday.game_day.pk, board_member, random.Random(5))
    services.confirm(sunday.game_day.pk, board_member)
    return sunday


def times_tile(client: Client) -> tiles.Tile | None:
    response = client.get(reverse("board:dashboard"))
    return next(
        (t for g in response.context["groups"] for t in g.tiles if t.title == "Times"), None
    )


def drop(sunday: Sunday, player: Player, board: Player) -> None:
    games.leave_list(sunday.game_day.pk, player, board_actor=board)


class TestPainelTile:
    def test_confirmed_and_in_sync_needs_no_alert(
        self, confirmed: Sunday, board_client: Client
    ) -> None:
        assert not times_tile(board_client).needs_attention

    def test_a_list_change_after_confirming_asks_to_adjust(
        self, confirmed: Sunday, board_client: Client, board_member: Player
    ) -> None:
        drop(confirmed, confirmed.line[0], board_member)
        tile = times_tile(board_client)
        assert (tile.note, tile.tone) == ("Ajustar times", tiles.WAIT)

    def test_a_pote_change_after_confirming_asks_to_adjust(
        self, confirmed: Sunday, board_client: Client
    ) -> None:
        Player.objects.filter(pk=confirmed.line[0].pk).update(pote=5)
        assert times_tile(board_client).note == "Ajustar times"


class TestPoteChange:
    def test_who_changed_is_named_and_confirming_is_blocked(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        services.draw(sunday.game_day.pk, board_member, random.Random(5))
        player = sunday.line[0]
        old = player.pote
        Player.objects.filter(pk=player.pk).update(pote=5 if old != 5 else 1)

        with pytest.raises(services.MatchdayError, match="Os potes mudaram depois do sorteio"):
            services.confirm(sunday.game_day.pk, board_member)

    def test_keeping_the_teams_takes_the_new_potes_as_the_baseline(
        self, confirmed: Sunday, board_client: Client
    ) -> None:
        player = confirmed.line[0]
        Player.objects.filter(pk=player.pk).update(pote=5 if player.pote != 5 else 1)
        assert times_tile(board_client).note == "Ajustar times"

        response = board_client.post(reverse("board:accept_potes", kwargs={"day": DAY}))

        assert response.status_code == 302
        assert not times_tile(board_client).needs_attention

    def test_old_teams_without_a_kept_pote_are_never_flagged(
        self, confirmed: Sunday, board_client: Client
    ) -> None:
        LineupSpot.objects.update(pote=None)
        Player.objects.filter(pk=confirmed.line[0].pk).update(pote=5)
        assert not times_tile(board_client).needs_attention

    def test_moving_someone_by_hand_refreshes_the_potes(
        self, confirmed: Sunday, board_member: Player
    ) -> None:
        player = confirmed.line[0]
        Player.objects.filter(pk=player.pk).update(pote=5 if player.pote != 5 else 1)
        spots = {s.player_id: s.team.number if s.team else None for s in LineupSpot.objects.all()}
        services.assign(confirmed.game_day.pk, board_member, spots)
        assert services.in_sync(services.lineup_for(confirmed.game_day))


class TestPlayerSide:
    def test_a_player_sees_teams_in_adjustment_on_the_incomplete_team(
        self, confirmed: Sunday, board_member: Player, board_client: Client
    ) -> None:
        gone = confirmed.line[0]
        team = LineupSpot.objects.select_related("team").get(player=gone).team
        drop(confirmed, gone, board_member)
        viewer = confirmed.line[-1]
        client = Client()
        client.force_login(viewer)

        html = client.get(reverse("matchday:today")).content.decode()

        assert 'data-state="em-ajuste"' in html
        assert "Times em ajuste" in html
        assert html.count(">em ajuste<") == 1
        assert team.name in html

    def test_nothing_is_flagged_while_the_teams_match_the_list(self, confirmed: Sunday) -> None:
        client = Client()
        client.force_login(confirmed.line[-1])
        html = client.get(reverse("matchday:today")).content.decode()
        assert "em ajuste" not in html
