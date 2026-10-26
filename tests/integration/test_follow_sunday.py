"""Acompanhar o domingo: the Início card once the Sunday is over, and the way back of its screen."""

import random
from collections.abc import Callable
from datetime import datetime
from urllib.parse import quote

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from core.models import ClubSettings
from matchday import services as matchday
from matchday.domain.score import Side
from tests.conftest import SP, Sunday, end_game, start_game

pytestmark = pytest.mark.django_db

DAY = "2026-10-18"
KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)
AFTER_GAME = datetime(2026, 10, 18, 12, 30, tzinfo=SP)
HOME = reverse("games:home")
TODAY = reverse("matchday:today")
FOLLOW = f"{TODAY}?next={quote(HOME, safe='')}"
VOTE = reverse("community:gold_ball_day", kwargs={"day": DAY})
CAST = reverse("community:vote", kwargs={"day": DAY})


def login(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


def text(response) -> str:
    return response.content.decode()


@pytest.fixture
def sunday(
    fill_sunday: Callable[..., Sunday], board_member: Player, clock: FixedClock, club_values
) -> Sunday:
    sunday = fill_sunday()
    matchday.draw(sunday.game_day.pk, board_member, random.Random(3))
    matchday.confirm(sunday.game_day.pk, board_member)
    clock.set(KICKOFF)
    start_game(sunday.game_day)
    match = matchday.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)
    scorer = match.lineup.spots.filter(team=match.home_team).select_related("player")[0].player
    matchday.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
    matchday.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
    clock.set(datetime(2026, 10, 18, 11, 30, tzinfo=SP))
    end_game(sunday.game_day)
    sunday.game_day.refresh_from_db()
    return sunday


def hero_of(page: str) -> str:
    start = page.index('class="hero"')
    return page[start : page.index("</section>", start)]


class TestVenue:
    """The hero shows the venue the diretoria configured, and nothing when there is none."""

    def test_the_configured_venue_is_shown(self, sunday: Sunday) -> None:
        ClubSettings.objects.update_or_create(pk=1, defaults={"venue_name": "Campo do Zé"})
        assert "Campo do Zé" in hero_of(text(login(sunday.line[0]).get(HOME)))

    def test_without_a_venue_nothing_takes_its_place(self, sunday: Sunday) -> None:
        ClubSettings.objects.update_or_create(pk=1, defaults={"venue_name": ""})
        assert "hero__sub" not in hero_of(text(login(sunday.line[0]).get(HOME)))


class TestFinishedSunday:
    """The vote only follows the end of the Sunday, and the game card says so."""

    def test_while_the_sunday_is_on_there_is_no_vote(
        self, fill_sunday: Callable[..., Sunday], clock: FixedClock
    ) -> None:
        sunday = fill_sunday()
        clock.set(KICKOFF)
        page = text(login(sunday.line[0]).get(HOME))
        assert "Acompanhar o domingo" in hero_of(page)
        assert 'data-card="bola-de-ouro"' not in page

    def test_once_it_ends_the_card_stops_following_and_the_vote_opens(self, sunday: Sunday) -> None:
        page = text(login(sunday.line[0]).get(HOME))
        hero = hero_of(page)
        assert "Acompanhar o domingo" not in page.split('<nav class="tabbar"', 1)[0]
        assert "Domingo encerrado" in hero
        assert "Ver como foi o domingo" in hero
        assert f'href="{FOLLOW}"' in hero
        assert "votação aberta" in page

    def test_the_next_sunday_list_card_comes_before_the_recap_card(self, sunday: Sunday) -> None:
        page = text(login(sunday.line[0]).get(HOME))
        assert 'data-state="' in page
        assert page.index('data-state="') < page.index('class="hero"')

    def test_while_the_sunday_is_on_the_game_card_keeps_the_list_state_inside_it(
        self, fill_sunday: Callable[..., Sunday], clock: FixedClock
    ) -> None:
        sunday = fill_sunday()
        clock.set(KICKOFF)
        page = text(login(sunday.line[0]).get(HOME))
        assert 'data-state="' in hero_of(page)
        assert 'data-state="' not in page[: page.index('class="hero"')]

    def test_the_clock_never_ends_the_sunday(
        self, fill_sunday: Callable[..., Sunday], clock: FixedClock
    ) -> None:
        sunday = fill_sunday()
        clock.set(AFTER_GAME)
        start_game(sunday.game_day)
        page = text(login(sunday.line[0]).get(HOME))
        hero = hero_of(page)
        assert "Domingo encerrado" not in hero
        assert "Acompanhar o domingo" in hero
        sunday.game_day.refresh_from_db()
        assert not sunday.game_day.is_finished

    def test_the_card_keeps_saying_it_until_monday(self, sunday: Sunday, clock: FixedClock) -> None:
        clock.set(datetime(2026, 10, 19, 7, 0, tzinfo=SP))
        hero = hero_of(text(login(sunday.line[0]).get(HOME)))
        assert "Domingo encerrado" in hero
        assert "Acompanhar o domingo" not in hero


class TestWayBack:
    @pytest.mark.parametrize(
        "target", ["//evil.example/", "https://evil.example/", "javascript:alert(1)", "/\\evil"]
    )
    def test_only_paths_of_this_site_are_followed(self, sunday: Sunday, target: str) -> None:
        page = text(login(sunday.line[0]).get(TODAY, {"next": target}))
        assert "data-back" not in page
        assert "evil" not in page

    def test_voting_returns_to_the_vote_with_the_way_back(self, sunday: Sunday) -> None:
        voter, a, b, c = sunday.line[:4]
        origin = f"{TODAY}?next=%2F"
        response = login(voter).post(
            CAST, {"first": a.pk, "second": b.pk, "third": c.pk, "next": origin}
        )
        assert response.status_code == 302
        assert response["Location"] == f"{VOTE}?next={quote(origin, safe='')}"

    def test_voting_ignores_an_unsafe_way_back(self, sunday: Sunday) -> None:
        voter, a, b, c = sunday.line[:4]
        response = login(voter).post(
            CAST, {"first": a.pk, "second": b.pk, "third": c.pk, "next": "//evil.example/"}
        )
        assert response["Location"] == VOTE
