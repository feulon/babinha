"""What a player sees while the game runs: the match on court and their own next match."""

import random
import re
from collections.abc import Callable
from datetime import datetime

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from matchday import services
from matchday.domain.score import Side
from matchday.models import Lineup, LineupSpot
from tests.conftest import SP, Sunday, start_game

pytestmark = pytest.mark.django_db

AFTER_KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)
TODAY = reverse("matchday:today")


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday]) -> Sunday:
    return fill_sunday()


@pytest.fixture
def lineup(sunday: Sunday, board_member: Player) -> Lineup:
    drawn = services.draw(sunday.game_day.pk, board_member, random.Random(5))
    services.rename_teams(
        sunday.game_day.pk, board_member, {1: "Azul", 2: "Verde", 3: "Branco", 4: "Preto"}
    )
    return drawn


def login(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


def text(response) -> str:
    return response.content.decode()


def player_of(lineup: Lineup, number: int) -> Player:
    return LineupSpot.objects.filter(lineup=lineup, team__number=number).first().player


class TestFollowTheGame:
    @pytest.fixture
    def table(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, clock: FixedClock
    ) -> None:
        services.confirm(sunday.game_day.pk, board_member)
        for home, away in ((1, 2), (3, 4), (1, 3)):
            services.add_match(sunday.game_day.pk, board_member, home_team=home, away_team=away)
        clock.set(AFTER_KICKOFF)
        start_game()

    def score_first_match(self, sunday: Sunday, lineup: Lineup, board_member: Player) -> None:
        match = lineup.matches.get(order=1)
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=player_of(lineup, 1).pk)

    def test_the_match_on_court_and_my_next_one_come_first(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, table: None
    ) -> None:
        self.score_first_match(sunday, lineup, board_member)
        html = text(login(player_of(lineup, 3)).get(TODAY))
        court = re.search(r'data-state="agora".*?</section>', html, re.S).group(0)
        assert "Em andamento" in court
        assert "Azul" in court
        assert "1 x 0" in court
        mine = re.search(r'data-state="meu-jogo".*?</section>', html, re.S).group(0)
        assert "Seu próximo jogo" in mine
        assert "Branco" in mine
        assert "Preto" in mine

    def test_my_own_match_on_court_is_one_block(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, table: None
    ) -> None:
        self.score_first_match(sunday, lineup, board_member)
        html = text(login(player_of(lineup, 1)).get(TODAY))
        assert "Em andamento · seu time" in html
        assert 'data-state="meu-jogo"' not in html
