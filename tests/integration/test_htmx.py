"""Views answer htmx with an HTML fragment and a plain form post with a redirect."""

import random
from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from games import services as services_games
from matchday import services
from matchday.domain.score import Side
from matchday.models import Goal, LineupSpot, Match
from tests.conftest import Sunday, end_game

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
DAY = "2026-10-18"


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday]) -> Sunday:
    return fill_sunday()


def text(response) -> str:
    return response.content.decode()


class TestHomeJoin:
    def test_htmx_join_returns_the_home_block_only(
        self, client: Client, make_player: Callable[..., Player], clock: FixedClock
    ) -> None:
        player = make_player("Ronaldinho")
        client.force_login(player)
        response = client.post(reverse("games:join"), **HTMX)
        html = text(response)
        assert response.status_code == 200
        assert "<html" not in html
        assert 'id="home"' in html
        assert 'data-state="lista"' in html
        assert 'id="messages"' in html
        assert "Você está na lista deste domingo!" in html

    def test_plain_join_still_redirects(
        self, client: Client, make_player: Callable[..., Player], clock: FixedClock
    ) -> None:
        client.force_login(make_player("Ronaldinho"))
        response = client.post(reverse("games:join"))
        assert response.status_code == 302
        assert response["Location"] == reverse("games:home")

    def test_the_partial_points_to_payments_instead_of_carrying_the_receipt_form(
        self,
        client: Client,
        make_player: Callable[..., Player],
        clock: FixedClock,
        club_values,
    ) -> None:
        client.force_login(make_player("Garrincha"))
        client.post(reverse("games:join"), **HTMX)
        end_game(services_games.current_game_day())
        html = text(client.get(reverse("games:home"), **HTMX))
        assert f'<a href="{reverse("finance:payments")}?next=' in html
        assert "multipart/form-data" not in html


class TestBoardRoster:
    def test_remove_from_list_swaps_the_roster_block(
        self, sunday: Sunday, board_client: Client
    ) -> None:
        player = sunday.line[0]
        url = reverse("board:remove_from_list", kwargs={"day": DAY, "player_id": player.pk})
        response = board_client.post(url, **HTMX)
        html = text(response)
        assert response.status_code == 200
        assert "<html" not in html
        assert 'id="roster-board"' in html
        assert f"Retirar {player.name}" not in html
        assert 'id="messages"' in html

    def test_plain_remove_still_redirects(self, sunday: Sunday, board_client: Client) -> None:
        url = reverse("board:remove_from_list", kwargs={"day": DAY, "player_id": sunday.line[0].pk})
        response = board_client.post(url)
        assert response.status_code == 302
        assert response["Location"] == reverse("board:game_day", kwargs={"day": DAY})

    def test_add_without_a_choice_reports_the_error_in_the_block(
        self, sunday: Sunday, board_client: Client
    ) -> None:
        response = board_client.post(reverse("board:add_to_list", kwargs={"day": DAY}), **HTMX)
        assert response.status_code == 200
        assert "Escolha um jogador." in text(response)

    def test_the_block_needs_the_board(self, sunday: Sunday, client: Client) -> None:
        client.force_login(sunday.line[0])
        url = reverse("board:remove_from_list", kwargs={"day": DAY, "player_id": sunday.line[1].pk})
        assert client.post(url, **HTMX).status_code != 200


@pytest.fixture
def match(sunday: Sunday, board_member: Player, after_kickoff: FixedClock) -> Match:
    services.draw(sunday.game_day.pk, board_member, random.Random(5))
    return services.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)


def home_players(match: Match) -> list[Player]:
    spots = LineupSpot.objects.filter(lineup=match.lineup, team=match.home_team)
    return [spot.player for spot in spots]


class TestMatchGoals:
    def test_goal_swaps_the_live_block(self, match: Match, board_client: Client) -> None:
        scorer = home_players(match)[0]
        url = reverse("board:add_goal", kwargs={"match_id": match.pk, "side": "home"})
        response = board_client.post(url, {"scorer": scorer.pk, "assist": ""}, **HTMX)
        html = text(response)
        assert response.status_code == 200
        assert "<html" not in html
        assert 'id="match-live"' in html
        assert "Gol do Time A!" in html
        assert scorer.name in html
        assert Goal.objects.count() == 1

    def test_invalid_goal_shows_the_error_in_the_block(
        self, match: Match, board_client: Client
    ) -> None:
        url = reverse("board:add_goal", kwargs={"match_id": match.pk, "side": "home"})
        response = board_client.post(url, {"scorer": ""}, **HTMX)
        assert response.status_code == 200
        assert 'id="match-live"' in text(response)
        assert "Este campo é obrigatório." in text(response)
        assert not Goal.objects.exists()

    def test_the_winning_goal_reloads_the_page_to_stop_the_timer(
        self, match: Match, board_client: Client, board_member: Player
    ) -> None:
        scorer = home_players(match)[0]
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
        url = reverse("board:add_goal", kwargs={"match_id": match.pk, "side": "home"})
        response = board_client.post(url, {"scorer": scorer.pk, "assist": ""}, **HTMX)
        assert response.status_code == 204
        assert response["HX-Redirect"] == reverse("board:match", kwargs={"match_id": match.pk})

    def test_deleting_the_winning_goal_reloads_to_restart_the_timer(
        self, match: Match, board_client: Client, board_member: Player
    ) -> None:
        scorer = home_players(match)[0]
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
        last = services.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
        services.reopen_match(match.pk, board_member)
        url = reverse("board:delete_goal", kwargs={"match_id": match.pk, "goal_id": last.pk})
        response = board_client.post(url, **HTMX)
        assert response.status_code == 204
        assert response["HX-Redirect"] == reverse("board:match", kwargs={"match_id": match.pk})

    def test_deleting_a_goal_swaps_the_live_block(
        self, match: Match, board_client: Client, board_member: Player
    ) -> None:
        scorer = home_players(match)[0]
        goal = services.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
        url = reverse("board:delete_goal", kwargs={"match_id": match.pk, "goal_id": goal.pk})
        response = board_client.post(url, **HTMX)
        assert response.status_code == 200
        assert "Gol apagado." in text(response)
        assert not Goal.objects.exists()

    def test_plain_goal_still_redirects(self, match: Match, board_client: Client) -> None:
        url = reverse("board:add_goal", kwargs={"match_id": match.pk, "side": "home"})
        response = board_client.post(url, {"scorer": home_players(match)[0].pk})
        assert response.status_code == 302


class TestSessionEndedDuringAnAction:
    """An action sent after the session ended goes to the login page, not into the screen."""

    def test_htmx_action_without_a_session_redirects_the_whole_page(self, client: Client) -> None:
        response = client.post(
            reverse("games:join"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_CURRENT_URL="http://testserver/diretoria/domingo/2026-10-18/?x=1",
        )
        assert response.status_code == 204
        assert response["HX-Redirect"] == (
            "/entrar/?next=%2Fdiretoria%2Fdomingo%2F2026-10-18%2F%3Fx%3D1"
        )
        assert "Location" not in response

    def test_without_a_known_screen_it_keeps_the_login_redirect_target(
        self, client: Client
    ) -> None:
        response = client.post(reverse("games:join"), HTTP_HX_REQUEST="true")
        assert response.status_code == 204
        assert response["HX-Redirect"].startswith("/entrar/?next=")

    def test_current_url_on_another_host_is_not_trusted(self, client: Client) -> None:
        response = client.post(
            reverse("games:join"),
            HTTP_HX_REQUEST="true",
            HTTP_HX_CURRENT_URL="https://evil.example/entrar/",
        )
        assert "evil.example" not in response["HX-Redirect"]

    def test_plain_form_post_still_gets_a_normal_redirect(self, client: Client) -> None:
        response = client.post(reverse("games:join"))
        assert response.status_code == 302
        assert response["Location"].startswith("/entrar/")

    def test_htmx_action_in_a_session_is_untouched(
        self, client: Client, make_player: Callable[..., Player], clock: FixedClock
    ) -> None:
        client.force_login(make_player("Ronaldinho"))
        response = client.post(reverse("games:join"), **HTMX)
        assert response.status_code == 200
        assert "HX-Redirect" not in response


class TestCsrfCookieForScripts:
    """The scripts send the current CSRF token read from the cookie, so every page names it."""

    def test_the_page_names_the_csrf_cookie_the_scripts_read(self, client: Client) -> None:
        html = text(client.get(reverse("accounts:login")))
        assert '<meta name="csrf-cookie" content="csrftoken">' in html

    def test_the_demo_cookie_name_is_named_too(self, client: Client, settings) -> None:
        settings.CSRF_COOKIE_NAME = "baba_demo_csrftoken"
        html = text(client.get(reverse("accounts:login")))
        assert '<meta name="csrf-cookie" content="baba_demo_csrftoken">' in html
