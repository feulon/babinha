"""Início: the list lives inside the Sunday card, and "Acompanhar o domingo" waits for the day."""

from collections.abc import Callable
from datetime import date, datetime

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from games import services as games
from tests.conftest import SP, end_game

pytestmark = pytest.mark.django_db

HOME = reverse("games:home")
SATURDAY_NIGHT = datetime(2026, 10, 17, 23, 59, tzinfo=SP)
SUNDAY_START = datetime(2026, 10, 18, 0, 0, tzinfo=SP)
WEDNESDAY = datetime(2026, 10, 14, 21, 0, tzinfo=SP)


@pytest.fixture
def player(make_player: Callable[..., Player], president: Player) -> Player:
    return make_player("Ronaldinho", referred_by=president)


@pytest.fixture
def player_client(client: Client, player: Player) -> Client:
    client.force_login(player)
    return client


def hero_of(client: Client) -> str:
    page = client.get(HOME).content.decode()
    start = page.index('class="hero"')
    return page[start : page.index("</section>", start)]


class TestListInsideTheCard:
    def test_the_state_and_the_join_action_are_in_the_sunday_card(
        self, player_client: Client, clock: FixedClock
    ) -> None:
        clock.set(WEDNESDAY)
        hero = hero_of(player_client)
        assert 'data-state="fora"' in hero
        assert reverse("games:join") in hero
        assert "Entrar na lista" in hero

    def test_there_is_no_card_of_the_list_outside_it(
        self, player_client: Client, clock: FixedClock
    ) -> None:
        clock.set(WEDNESDAY)
        page = player_client.get(HOME).content.decode()
        assert page.count('data-state="fora"') == 1
        assert page.index('data-state="fora"') < page.index("</section>", page.index("data-hero"))

    def test_the_leave_action_is_in_the_card_once_in_the_list(
        self, player_client: Client, clock: FixedClock
    ) -> None:
        clock.set(WEDNESDAY)
        player_client.post(reverse("games:join"))
        hero = hero_of(player_client)
        assert 'data-state="lista"' in hero
        assert reverse("games:leave") in hero
        assert "Desistir" in hero

    def test_while_the_finished_sunday_is_shown_the_next_list_stays_in_the_green_card(
        self, player_client: Client, clock: FixedClock
    ) -> None:
        end_game(games.game_day_for(date(2026, 10, 11)))
        clock.set(datetime(2026, 10, 12, 7, 0, tzinfo=SP))
        page = player_client.get(HOME).content.decode()
        recap = page[page.index('class="recap"') :]
        assert 'data-state="fechada"' in hero_of(player_client)
        assert "data-state" not in recap[: recap.index("</section>")]


class TestFollowButton:
    def test_before_the_sunday_it_is_locked_and_not_a_link(
        self, player_client: Client, clock: FixedClock
    ) -> None:
        clock.set(SATURDAY_NIGHT)
        hero = hero_of(player_client)
        assert "Acompanhar o domingo" in hero
        assert 'aria-disabled="true"' in hero
        assert reverse("matchday:today") not in hero

    def test_from_the_start_of_the_sunday_it_is_a_link(
        self, player_client: Client, clock: FixedClock
    ) -> None:
        clock.set(SUNDAY_START)
        hero = hero_of(player_client)
        assert "Acompanhar o domingo" in hero
        assert "aria-disabled" not in hero
        assert f'href="{reverse("matchday:today")}?next=' in hero

    def test_in_demo_mode_it_is_open_on_any_day(
        self, player_client: Client, clock: FixedClock, settings
    ) -> None:
        settings.BABA_DEMO = True
        clock.set(WEDNESDAY)
        hero = hero_of(player_client)
        assert "aria-disabled" not in hero
        assert reverse("matchday:today") in hero
