"""Finding the Bola de Ouro: the badge on the tab and the board's shortcut, lit only while the
vote is open."""

import random
from collections.abc import Callable
from datetime import datetime

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from community import selectors
from core.clock import FixedClock
from matchday import services as matchday
from matchday.domain.score import Side
from tests.conftest import SP, Sunday, end_game

pytestmark = pytest.mark.django_db

KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)
AFTER_GAME = datetime(2026, 10, 18, 11, 30, tzinfo=SP)
MONDAY_LIST_OPENS = datetime(2026, 10, 19, 8, 0, tzinfo=SP)
GOLD_BALL = reverse("community:gold_ball")
RANKING = reverse("community:ranking")
PLAYERS = reverse("community:players")
DOT = 'role="img" aria-label="votação aberta"'
TAB_BADGE = 'aria-label="Votação da Bola de Ouro aberta"'


@pytest.fixture
def played(fill_sunday: Callable[..., Sunday], board_member: Player, clock: FixedClock) -> Sunday:
    sunday = fill_sunday()
    matchday.draw(sunday.game_day.pk, board_member, random.Random(3))
    matchday.confirm(sunday.game_day.pk, board_member)
    clock.set(KICKOFF)
    match = matchday.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)
    scorer = match.lineup.spots.filter(team=match.home_team).select_related("player")[0].player
    for _ in range(2):
        matchday.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
    clock.set(AFTER_GAME)
    end_game(sunday.game_day)
    return sunday


def get(player: Player, url: str) -> str:
    client = Client()
    client.force_login(player)
    return client.get(url).content.decode()


class TestBadge:
    def test_nothing_lit_without_a_vote(self, make_player, clock: FixedClock) -> None:
        page = get(make_player("Zico"), RANKING)
        assert DOT not in page
        assert TAB_BADGE not in page

    def test_lit_while_the_vote_is_open(self, played: Sunday, clock: FixedClock) -> None:
        clock.set(AFTER_GAME)
        viewer = played.line[0]
        assert selectors.open_voting_day() == played.game_day
        for url in (RANKING, GOLD_BALL, reverse("games:home")):
            page = get(viewer, url)
            assert TAB_BADGE in page
        assert DOT in get(viewer, RANKING)

    def test_lit_for_players_who_cannot_vote_too(
        self, played: Sunday, clock: FixedClock, make_player
    ) -> None:
        clock.set(AFTER_GAME)
        assert TAB_BADGE in get(make_player("Torcedor"), RANKING)

    def test_off_once_the_vote_closes(self, played: Sunday, clock: FixedClock) -> None:
        clock.set(MONDAY_LIST_OPENS)
        assert selectors.open_voting_day() is None
        assert TAB_BADGE not in get(played.line[0], RANKING)

    def test_off_before_the_last_match_ends(self, played: Sunday, clock: FixedClock) -> None:
        clock.set(datetime(2026, 10, 18, 9, 0, tzinfo=SP))
        assert selectors.open_voting_day() is None


class TestBoardShortcut:
    def tile(self, board_member: Player):
        client = Client()
        client.force_login(board_member)
        response = client.get(reverse("board:dashboard"))
        return next(
            (t for g in response.context["groups"] for t in g.tiles if t.title == "Bola de Ouro"),
            None,
        )

    def test_the_tile_is_quiet_without_an_open_vote(
        self, board_member: Player, clock: FixedClock
    ) -> None:
        tile = self.tile(board_member)
        assert (tile.note, tile.needs_attention) == ("", False)

    def test_the_tile_says_when_the_vote_is_open(
        self, played: Sunday, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(AFTER_GAME)
        tile = self.tile(board_member)
        assert tile.url == GOLD_BALL
        assert (tile.note, tile.needs_attention) == ("Votação aberta", True)
