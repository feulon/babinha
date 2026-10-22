"""The home screen does not advertise the free dropout period, so it does not invite dropouts.
The rule still holds and the rules page and the sign-up still explain it."""

from collections.abc import Callable
from datetime import datetime

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from games import services as games
from games.models import ListEntry
from tests.conftest import SP

pytestmark = pytest.mark.django_db

HOME = reverse("games:home")
SUNDAY_BEFORE_KICKOFF = datetime(2026, 10, 18, 7, 0, tzinfo=SP)
DEADLINE_TEXTS = ("sem custo", "sem multa", "23h59")


def home(client: Client) -> str:
    return client.get(HOME).content.decode()


def test_on_the_list_shows_desistir_without_the_deadline(
    client: Client, make_player: Callable[..., Player], clock: FixedClock
) -> None:
    player = make_player("Ronaldinho")
    games.join_list(games.current_game_day().pk, player)
    client.force_login(player)
    page = home(client)
    assert 'data-state="lista"' in page
    assert ">Desistir</a>" in page
    assert not any(text in page for text in DEADLINE_TEXTS)


def test_promoted_shows_recusar_without_the_deadline(
    client: Client, make_player: Callable[..., Player], clock: FixedClock
) -> None:
    player = make_player("Ronaldinho")
    games.join_list(games.current_game_day().pk, player)
    ListEntry.objects.filter(player=player).update(promoted=True)
    client.force_login(player)
    page = home(client)
    assert 'data-state="promovido"' in page
    assert ">Recusar a vaga</a>" in page
    assert not any(text in page for text in DEADLINE_TEXTS)


def test_after_the_deadline_the_home_says_nothing_about_it(
    client: Client, make_player: Callable[..., Player], clock: FixedClock
) -> None:
    player = make_player("Ronaldinho")
    games.join_list(games.current_game_day().pk, player)
    clock.set(SUNDAY_BEFORE_KICKOFF)
    client.force_login(player)
    page = home(client)
    assert ">Desistir</a>" in page
    assert not any(text in page for text in DEADLINE_TEXTS)


def test_the_rules_page_still_explains_the_deadline(client: Client) -> None:
    page = client.get(reverse("rules")).content.decode()
    assert "não custa nada até sábado às 23h59" in page
