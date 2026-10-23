import random
import re
from collections.abc import Callable
from datetime import datetime, time

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from community import services
from community.models import Ballot, PlaceTally, Season
from core.clock import FixedClock
from games.models import GameDay
from matchday import services as matchday
from matchday.domain.score import Side
from tests.conftest import SP, Sunday, end_game

pytestmark = pytest.mark.django_db

DAY = "2026-10-18"
KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)
AFTER_GAME = datetime(2026, 10, 18, 11, 30, tzinfo=SP)
MONDAY_LIST_OPENS = datetime(2026, 10, 19, 8, 0, tzinfo=SP)
VOTE = reverse("community:gold_ball_day", kwargs={"day": DAY})
CAST = reverse("community:vote", kwargs={"day": DAY})
RANKING = reverse("community:ranking")
SEASON = reverse("community:season")
PLAYERS = reverse("community:players")


def login(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


def text(response) -> str:
    return response.content.decode()


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday], board_member: Player, clock: FixedClock) -> Sunday:
    sunday = fill_sunday()
    matchday.draw(sunday.game_day.pk, board_member, random.Random(3))
    matchday.confirm(sunday.game_day.pk, board_member)
    clock.set(KICKOFF)
    match = matchday.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)
    spots = match.lineup.spots.filter(team=match.home_team).select_related("player")
    scorer = spots[0].player
    matchday.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
    matchday.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
    clock.set(AFTER_GAME)
    end_game(sunday.game_day)
    sunday.game_day.refresh_from_db()
    return sunday


class TestAccess:
    @pytest.mark.parametrize(
        "url",
        [
            VOTE,
            reverse("community:gold_ball"),
            RANKING,
            reverse("community:player", kwargs={"player_id": 1}),
            reverse("community:season"),
        ],
    )
    def test_login_required(self, client: Client, url: str) -> None:
        response = client.get(url)
        assert response.status_code == 302
        assert reverse("accounts:login") in response["Location"]

    def test_voting_requires_login(self, client: Client, sunday: Sunday) -> None:
        assert client.post(CAST).status_code == 302
        assert not Ballot.objects.exists()

    def test_season_screen_is_for_the_board(self, sunday: Sunday, board_member: Player) -> None:
        player = login(sunday.line[0])
        assert player.get(reverse("community:season")).status_code == 403
        assert player.post(reverse("community:end_season")).status_code == 403
        assert Season.objects.filter(ends_on__isnull=False).count() == 0
        assert login(board_member).get(reverse("community:season")).status_code == 200


class TestVoteScreen:
    def test_finished_matches_do_not_allow_voting_before_the_sunday_ends(
        self, sunday: Sunday, clock: FixedClock
    ) -> None:
        GameDay.objects.filter(pk=sunday.game_day.pk).update(finished_at=None, finished_by=None)
        clock.set(datetime(2026, 10, 18, 12, 0, tzinfo=SP))
        voter, a, b, c = sunday.line[:4]
        client = login(voter)
        page = text(client.get(VOTE))
        assert 'data-state="aguardando"' in page
        assert 'name="first"' not in page
        response = client.post(CAST, {"first": a.pk, "second": b.pk, "third": c.pk}, follow=True)
        assert "A votação ainda não abriu: ela abre quando o futebol for encerrado." in text(
            response
        )
        assert not Ballot.objects.exists()
        assert not PlaceTally.objects.exists()

    def test_automatic_sunday_end_opens_voting(self, sunday: Sunday, clock: FixedClock) -> None:
        GameDay.objects.filter(pk=sunday.game_day.pk).update(finished_at=None, finished_by=None)
        clock.set(datetime(2026, 10, 18, 12, 30, tzinfo=SP))
        page = text(login(sunday.line[0]).get(VOTE))
        assert 'data-state="votar"' in page

    def test_voter_sees_the_form_without_themselves(self, sunday: Sunday) -> None:
        viewer = sunday.line[0]
        page = text(login(viewer).get(VOTE))
        assert 'data-state="votar"' in page
        assert f'<option value="{sunday.line[1].pk}">' in page
        assert f'<option value="{viewer.pk}">' not in page

    def test_casting_a_vote(self, sunday: Sunday) -> None:
        voter, a, b, c = sunday.line[:4]
        client = login(voter)
        response = client.post(CAST, {"first": a.pk, "second": b.pk, "third": c.pk}, follow=True)
        assert response.redirect_chain[-1][0] == VOTE
        page = text(response)
        assert "Voto registrado" in page  # the status card; no flash message on top
        assert 'data-state="votou"' in page
        assert Ballot.objects.filter(voter=voter).exists()
        assert PlaceTally.objects.count() == 3

    def test_invalid_vote_shows_why(self, sunday: Sunday) -> None:
        voter, a = sunday.line[:2]
        response = login(voter).post(
            CAST, {"first": a.pk, "second": a.pk, "third": voter.pk}, follow=True
        )
        assert "Não repita o mesmo jogador" in text(response)
        assert not Ballot.objects.exists()

    def test_refused_vote_goes_back_to_the_form_without_resending_it(self, sunday: Sunday) -> None:
        voter, a, b = sunday.line[:3]
        client = login(voter)
        response = client.post(CAST, {"first": a.pk, "second": a.pk, "third": b.pk})
        assert response.status_code == 302
        assert response["Location"] == VOTE

        page = text(client.get(VOTE))
        form = page[page.index('<form method="post" action="' + CAST) :]
        assert "Não repita o mesmo jogador" in form[: form.index("Votar</button>")]
        assert f'<option value="{a.pk}" selected>' in page  # the picks made are kept
        assert "Não repita o mesmo jogador" not in text(client.get(VOTE))  # shown once
        assert not Ballot.objects.exists()

    def test_garbage_input_is_just_a_missing_pick(self, sunday: Sunday) -> None:
        response = login(sunday.line[0]).post(CAST, {"first": "abc"}, follow=True)
        assert "Escolha os três jogadores" in text(response)

    def test_get_is_not_allowed_on_vote(self, sunday: Sunday) -> None:
        assert login(sunday.line[0]).get(CAST).status_code == 405

    def test_someone_who_did_not_play_cannot_vote(
        self, sunday: Sunday, make_player: Callable[..., Player]
    ) -> None:
        outsider = make_player("De fora")
        client = login(outsider)
        assert 'data-state="nao-jogou"' in text(client.get(VOTE))
        a, b, c = sunday.line[:3]
        response = client.post(CAST, {"first": a.pk, "second": b.pk, "third": c.pk}, follow=True)
        assert "Só quem jogou" in text(response)

    def test_unknown_or_bad_day(self, sunday: Sunday) -> None:
        client = login(sunday.line[0])
        assert (
            client.get(reverse("community:gold_ball_day", kwargs={"day": "2026-01-04"})).status_code
            == 404
        )
        assert (
            client.get(reverse("community:gold_ball_day", kwargs={"day": "abc"})).status_code == 404
        )

    def test_default_page_is_the_latest_vote(self, sunday: Sunday) -> None:
        page = text(login(sunday.line[0]).get(reverse("community:gold_ball")))
        assert 'data-state="votar"' in page

    def test_no_vote_yet_page(self, client: Client, board_member: Player) -> None:
        page = text(login(board_member).get(reverse("community:gold_ball")))
        assert 'data-state="sem-votacao"' in page


class TestVoteCount:
    def test_count_is_shown_to_voters_and_to_those_who_voted(self, sunday: Sunday) -> None:
        voter, a, b, c = sunday.line[:4]
        services.cast_vote(sunday.game_day.pk, sunday.line[5], first=a.pk, second=b.pk, third=c.pk)
        services.cast_vote(sunday.game_day.pk, sunday.line[6], first=a.pk, second=b.pk, third=c.pk)
        total = len(sunday.line) + len(sunday.goalkeepers)
        client = login(voter)
        assert f"2 de {total} votaram" in text(client.get(VOTE))
        client.post(CAST, {"first": a.pk, "second": b.pk, "third": c.pk})
        page = text(client.get(VOTE))
        assert 'data-state="votou"' in page
        assert f"3 de {total} votaram" in page

    def test_the_live_refresh_gets_only_the_count(self, sunday: Sunday) -> None:
        response = login(sunday.line[0]).get(VOTE, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert "votaram" in text(response)
        assert "<form" not in text(response)

    def test_count_refresh_reloads_the_page_once_closed(
        self, sunday: Sunday, clock: FixedClock
    ) -> None:
        clock.set(MONDAY_LIST_OPENS)
        response = login(sunday.line[0]).get(VOTE, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert response["HX-Refresh"] == "true"

    def test_no_count_once_closed(self, sunday: Sunday, clock: FixedClock) -> None:
        clock.set(MONDAY_LIST_OPENS)
        assert "votaram" not in text(login(sunday.line[0]).get(VOTE))


class TestResultTime:
    """The result comes out when the next list opens, Monday 8h, not when everyone has voted."""

    BEFORE = datetime(2026, 10, 19, 7, 59, tzinfo=SP)

    def everyone_votes(self, sunday: Sunday) -> None:
        for n, voter in enumerate(sunday.line):
            picks = [p for p in sunday.line if p != voter]
            services.cast_vote(
                sunday.game_day.pk,
                voter,
                first=picks[n % len(picks)].pk,
                second=picks[(n + 1) % len(picks)].pk,
                third=picks[(n + 2) % len(picks)].pk,
            )

    def test_stays_open_for_everyone_until_monday_8h(
        self, sunday: Sunday, clock: FixedClock
    ) -> None:
        self.everyone_votes(sunday)
        clock.set(self.BEFORE)
        page = text(login(sunday.line[0]).get(VOTE))
        assert 'data-state="votou"' in page
        assert "pts" not in page
        assert "O resultado sai na segunda, 19/10 às 08h00." in page

    def test_the_result_comes_out_at_8h(self, sunday: Sunday, clock: FixedClock) -> None:
        self.everyone_votes(sunday)
        clock.set(MONDAY_LIST_OPENS)
        assert 'data-state="resultado"' in text(login(sunday.line[0]).get(VOTE))

    def test_the_hour_follows_the_hour_the_list_opens(
        self, sunday: Sunday, clock: FixedClock, settings
    ) -> None:
        settings.BABA_LIST_OPENS_AT = time(7, 30)
        self.everyone_votes(sunday)
        clock.set(datetime(2026, 10, 19, 7, 29, tzinfo=SP))
        assert "pts" not in text(login(sunday.line[0]).get(VOTE))
        clock.set(datetime(2026, 10, 19, 7, 30, tzinfo=SP))
        assert "pts" in text(login(sunday.line[0]).get(VOTE))


class TestAnonymity:
    def test_nobody_sees_who_voted_for_whom(
        self, sunday: Sunday, board_member: Player, clock: FixedClock
    ) -> None:
        winner, other, third = sunday.line[:3]
        voters = sunday.line[3:8]
        for voter in voters:
            login(voter).post(CAST, {"first": winner.pk, "second": other.pk, "third": third.pk})
        clock.set(MONDAY_LIST_OPENS)
        for viewer in (board_member, voters[0], winner):
            page = text(login(viewer).get(VOTE))
            assert 'data-state="resultado"' in page
            assert "15 pts" in page
            for voter in voters:
                if voter.name != viewer.name:
                    assert voter.name not in page


class TestResults:
    def test_complete_ties_share_the_winner_and_position(
        self, sunday: Sunday, clock: FixedClock
    ) -> None:
        a, b, c = sunday.line[:3]
        services.cast_vote(sunday.game_day.pk, sunday.line[3], first=a.pk, second=b.pk, third=c.pk)
        services.cast_vote(sunday.game_day.pk, sunday.line[4], first=b.pk, second=a.pk, third=c.pk)
        clock.set(MONDAY_LIST_OPENS)
        response = login(sunday.line[5]).get(VOTE)
        view = response.context["view"]
        assert {player.pk for player in view.winners} == {a.pk, b.pk}
        assert [row.position for row in view.results] == [1, 1, 3]
        assert [row.standing.points for row in view.results] == [8, 8, 4]
        assert "Empate: dividem a Bola de Ouro." in text(response)

    def test_results_after_the_vote_closes(self, sunday: Sunday, clock: FixedClock) -> None:
        winner, other, third = sunday.line[:3]
        for voter in sunday.line[3:6]:
            services.cast_vote(
                sunday.game_day.pk, voter, first=winner.pk, second=other.pk, third=third.pk
            )
        client = login(sunday.line[10])
        assert "pts" not in text(client.get(VOTE))
        clock.set(MONDAY_LIST_OPENS)
        page = text(client.get(VOTE))
        assert winner.name in page
        assert "3x 1º" in page
        assert "Bolas de Ouro" in page

    def test_results_show_5_3_2_points_and_place_counts(
        self, sunday: Sunday, clock: FixedClock
    ) -> None:
        a, b, c = sunday.line[:3]
        services.cast_vote(sunday.game_day.pk, sunday.line[5], first=a.pk, second=b.pk, third=c.pk)
        clock.set(MONDAY_LIST_OPENS)
        page = text(login(sunday.line[10]).get(VOTE))
        assert "5 pts" in page
        assert "3 pts" in page
        assert "2 pts" in page
        assert "1x 1º" in page
        assert "1x 2º" in page
        assert "1x 3º" in page
        assert "Contagem de votos" in page

    def test_sunday_with_no_votes(self, sunday: Sunday, clock: FixedClock) -> None:
        clock.set(MONDAY_LIST_OPENS)
        assert 'data-state="sem-votos"' in text(login(sunday.line[0]).get(VOTE))


class TestNicknamesInTheCommunity:
    def test_ranking_goals_and_gold_ball_show_nicknames_not_names(self, sunday: Sunday) -> None:
        for player in (*sunday.line, *sunday.goalkeepers):
            Player.objects.filter(pk=player.pk).update(
                nickname=player.name.replace("Linha", "Apelido").replace("Goleiro", "Manopla")
            )
        client = login(sunday.line[0])
        for url in (RANKING, VOTE, reverse("community:players")):
            page = text(client.get(url))
            assert "Apelido " in page, url
            assert not re.search(r"Linha \d\d", page), url

    def test_the_match_report_names_scorers_by_nickname(self, sunday: Sunday) -> None:
        for player in sunday.line:
            Player.objects.filter(pk=player.pk).update(nickname=player.name.replace("Linha", "Ap"))
        page = text(login(sunday.line[0]).get(reverse("matchday:today")))
        assert re.search(r"Ap \d\d", page)
        assert not re.search(r"Linha \d\d", page)


class TestRanking:
    def test_table_and_sorting(self, sunday: Sunday) -> None:
        client = login(sunday.line[0])
        page = text(client.get(RANKING))
        assert "Temporada 1" in page
        assert "1 domingo jogado" in page
        by_goals = text(client.get(RANKING, {"ordem": "gols"}))
        first_row = by_goals.split("<tbody>")[1].split("</tr>")[0]
        assert 'href="/jogador/' in first_row
        assert "<td>1</td>" in first_row

    def test_seasons_and_overall(self, sunday: Sunday, board_member: Player) -> None:
        services.end_season(board_member)
        client = login(sunday.line[0])
        old = text(client.get(RANKING, {"temporada": "1"}))
        assert "Temporada 1 (encerrada)" in old
        assert "1 domingo jogado" in old
        assert "Ainda não há jogos nesta temporada" in text(client.get(RANKING))
        assert "Todas as temporadas" in text(client.get(RANKING, {"temporada": "geral"}))

    @pytest.mark.parametrize("params", [{"temporada": "9"}, {"temporada": "x"}, {"ordem": "nome"}])
    def test_unknown_options(self, sunday: Sunday, params: dict) -> None:
        assert login(sunday.line[0]).get(RANKING, params).status_code == 404


class TestPlayerPage:
    def test_history(self, sunday: Sunday) -> None:
        scorer = sunday.line[0]
        spot = scorer.lineup_spots.get()
        page = text(
            login(sunday.line[5]).get(reverse("community:player", kwargs={"player_id": scorer.pk}))
        )
        assert scorer.name in page
        assert "18/10/2026" in page
        assert spot.team.name in page

    def test_unknown_player(self, sunday: Sunday) -> None:
        assert (
            login(sunday.line[0])
            .get(reverse("community:player", kwargs={"player_id": 99999}))
            .status_code
            == 404
        )


class TestSeasonScreen:
    def test_ending_the_season(self, sunday: Sunday, president: Player) -> None:
        client = login(president)
        assert "Encerrar temporada" not in text(client.get(RANKING))
        page = text(client.get(SEASON))
        assert "Encerrar temporada 1" in page
        assert "votação da Bola de Ouro ainda está aberta" in page
        response = client.post(reverse("community:end_season"), follow=True)
        assert "Começou a temporada 2" in text(response)
        assert "Temporada 1 · até" in text(response)
        assert Season.objects.get(number=2).is_open

    def test_second_end_the_same_day_is_refused(self, sunday: Sunday, president: Player) -> None:
        client = login(president)
        client.post(reverse("community:end_season"))
        response = client.post(reverse("community:end_season"), follow=True)
        assert "encerrada hoje" in text(response)
        assert Season.objects.count() == 2

    def test_only_the_president_ends_the_season(self, sunday: Sunday, board_member: Player) -> None:
        client = login(board_member)
        page = text(client.get(SEASON))
        assert "Só o presidente encerra a temporada." in page
        assert reverse("community:end_season") not in page
        assert client.post(reverse("community:end_season")).status_code == 403
        assert Season.objects.filter(ends_on__isnull=False).count() == 0


class TestHome:
    HOME = reverse("games:home")

    def test_card_nudges_to_vote_then_confirms(self, sunday: Sunday) -> None:
        voter, a, b, c = sunday.line[:4]
        client = login(voter)
        assert "Votar" in text(client.get(self.HOME))
        client.post(CAST, {"first": a.pk, "second": b.pk, "third": c.pk})
        page = text(client.get(self.HOME))
        assert "Voto registrado" in page
        assert 'data-card="bola-de-ouro"' in page

    def test_no_vote_button_for_someone_who_cannot_vote(
        self, sunday: Sunday, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Only three played: nobody has three others to pick, so the card must not invite.
        trio = sunday.line[:3]
        monkeypatch.setattr("community.selectors.eligible_voters", lambda _day: trio)
        page = text(login(trio[0]).get(self.HOME))
        assert 'data-card="bola-de-ouro"' not in page
        assert VOTE not in page

    def test_no_card_for_someone_who_did_not_play(
        self, sunday: Sunday, make_player: Callable[..., Player]
    ) -> None:
        assert 'data-card="bola-de-ouro"' not in text(login(make_player("De fora")).get(self.HOME))

    def test_card_shows_the_winner_after_the_vote(self, sunday: Sunday, clock: FixedClock) -> None:
        winner, other, third = sunday.line[:3]
        for voter in sunday.line[3:6]:
            services.cast_vote(
                sunday.game_day.pk, voter, first=winner.pk, second=other.pk, third=third.pk
            )
        clock.set(MONDAY_LIST_OPENS)
        page = text(login(sunday.line[10]).get(self.HOME))
        assert winner.name in page
        assert 'data-card="bola-de-ouro"' in page
