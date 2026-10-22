"""Opening a diretoria Sunday by address only creates a real, reasonable Sunday."""

from datetime import date

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from games.models import GameDay

pytestmark = pytest.mark.django_db


@pytest.fixture
def board_client(board_member: Player, clock) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


def open_day(client: Client, day: str, view: str = "board:game_day"):
    return client.get(reverse(view, kwargs={"day": day}))


class TestWindow:
    @pytest.mark.parametrize("day", ["2026-10-18", "2026-10-11", "2026-10-25", "2026-11-15"])
    def test_recent_and_coming_sundays_open(self, board_client: Client, day: str) -> None:
        assert open_day(board_client, day).status_code == 200
        assert GameDay.objects.filter(date=date.fromisoformat(day)).exists()

    @pytest.mark.parametrize(
        "day",
        [
            "2030-01-06",  # a Sunday, far ahead
            "2020-01-05",  # a Sunday, long gone
            "2026-11-22",  # five weeks ahead
            "2026-10-04",  # two weeks back
        ],
    )
    def test_a_far_sunday_that_never_existed_is_not_created(
        self, board_client: Client, day: str
    ) -> None:
        response = open_day(board_client, day)
        assert response.status_code == 404
        assert not GameDay.objects.filter(date=date.fromisoformat(day)).exists()

    @pytest.mark.parametrize("day", ["2026-10-20", "banana", "2026-13-40"])
    def test_a_day_that_is_not_a_sunday_is_a_friendly_404(
        self, board_client: Client, day: str
    ) -> None:
        response = open_day(board_client, day)
        assert response.status_code == 404
        html = response.content.decode()
        assert "Não existe um domingo do Babinha nessa data" in html
        assert f'href="{reverse("board:game_day", kwargs={"day": "2026-10-18"})}"' in html
        assert 'class="topbar"' in html  # inside the app layout
        assert "Not Found" not in html

    def test_the_other_day_screens_answer_the_same_way(self, board_client: Client) -> None:
        for view in ("board:teams", "board:matches"):
            response = open_day(board_client, "2030-01-06", view)
            assert response.status_code == 404
            assert "Não existe um domingo do Babinha" in response.content.decode()

    def test_an_old_sunday_that_exists_stays_reachable(self, board_client: Client) -> None:
        GameDay.objects.create(date=date(2026, 8, 2))
        assert open_day(board_client, "2026-08-02").status_code == 200


class TestPostsKeepWorking:
    def test_an_unreachable_day_cannot_be_posted_to_either(
        self, board_client: Client, make_player
    ) -> None:
        player = make_player("Fulano")
        response = board_client.post(
            reverse("board:add_to_list", kwargs={"day": "2030-01-06"}), {"player": player.pk}
        )
        assert response.status_code == 404
        assert not GameDay.objects.filter(date=date(2030, 1, 6)).exists()
