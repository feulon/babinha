import random
import re
from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from games import services as games
from games.models import Attendance
from matchday import services
from matchday.domain.score import Side
from matchday.models import Goal, Lineup, LineupSpot, Match
from tests.conftest import SP, Sunday

pytestmark = pytest.mark.django_db

DAY = "2026-10-18"
AFTER_KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)
AFTER_GAME = datetime(2026, 10, 18, 12, 30, tzinfo=SP)
TODAY = reverse("matchday:today")


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday]) -> Sunday:
    return fill_sunday()


@pytest.fixture
def lineup(sunday: Sunday, board_member: Player) -> Lineup:
    return services.draw(sunday.game_day.pk, board_member, random.Random(5))


def login(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


def text(response) -> str:
    return response.content.decode()


def members_of(match: Match, side: Side) -> list[Player]:
    return services.side_scorers(match, side)


def team_number(lineup: Lineup, player: Player) -> int | None:
    spot = LineupSpot.objects.select_related("team").get(lineup=lineup, player=player)
    return spot.team.number if spot.team else None


class TestAccess:
    @pytest.mark.parametrize(
        "url",
        [
            reverse("board:teams", kwargs={"day": DAY}),
            reverse("board:matches", kwargs={"day": DAY}),
        ],
    )
    def test_players_cannot_open_board_screens(self, sunday: Sunday, url: str) -> None:
        assert login(sunday.line[0]).get(url).status_code == 403

    def test_players_cannot_draw_or_score(
        self, sunday: Sunday, lineup: Lineup, board_member: Player
    ) -> None:
        match = services.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)
        player = login(sunday.line[0])
        assert player.post(reverse("board:draw_teams", kwargs={"day": DAY})).status_code == 403
        url = reverse("board:add_goal", kwargs={"match_id": match.pk, "side": "home"})
        assert player.post(url, {"scorer": sunday.line[0].pk}).status_code == 403
        assert player.get(reverse("board:match", kwargs={"match_id": match.pk})).status_code == 403

    def test_day_page_requires_login(self, client: Client) -> None:
        response = client.get(TODAY)
        assert response.status_code == 302


class TestPlayerView:
    def test_draft_teams_are_hidden(self, sunday: Sunday, lineup: Lineup) -> None:
        html = text(login(sunday.line[0]).get(TODAY))
        assert 'data-state="sem-times"' in html
        assert "Nada por aqui ainda" in html
        assert "Ansioso? O domingo chega logo." in html
        assert "Time A" not in html

    def test_confirmed_teams_are_shown_without_potes(
        self, sunday: Sunday, lineup: Lineup, board_member: Player
    ) -> None:
        services.confirm(sunday.game_day.pk, board_member)
        me = sunday.line[0]
        my_team = lineup.teams.get(number=team_number(lineup, me)).name
        html = text(login(me).get(TODAY))
        assert "Nada por aqui ainda" not in html
        assert f"Você está no {my_team}" in html
        assert f"{me.name} (você)" in html
        assert all(player.name in html for player in sunday.line + sunday.goalkeepers)
        assert "pote" not in html.lower()
        assert not re.search(r"\bP[1-5]\b", html)

    def test_a_no_show_after_confirming_only_costs_the_fine(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, clock: FixedClock
    ) -> None:
        """Attendance is marked during the games: the published teams stay as they are."""
        services.confirm(sunday.game_day.pk, board_member)
        absent = LineupSpot.objects.filter(lineup=lineup, team__number=1).first().player
        clock.set(AFTER_KICKOFF)
        games.mark_attendance(sunday.game_day.pk, absent, Attendance.FALTOU, board_member)
        teammate = LineupSpot.objects.filter(lineup=lineup, team__number=2).first().player
        assert absent.name in text(login(teammate).get(TODAY))
        assert "Times em ajuste" not in text(login(teammate).get(TODAY))
        assert "Você está no" in text(login(absent).get(TODAY))
        board = text(login(board_member).get(reverse("board:teams", kwargs={"day": DAY})))
        assert "Saíram ou faltaram" not in board
        assert "A lista mudou depois do sorteio" not in board

    def test_a_no_show_before_confirming_leaves_the_draft(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, clock: FixedClock
    ) -> None:
        absent = LineupSpot.objects.filter(lineup=lineup, team__number=1).first().player
        clock.set(AFTER_KICKOFF)
        games.mark_attendance(sunday.game_day.pk, absent, Attendance.FALTOU, board_member)
        board = text(login(board_member).get(reverse("board:teams", kwargs={"day": DAY})))
        assert f"Saíram ou faltaram: {absent.name}." in board

    def test_before_the_first_game_players_see_who_plays_it(
        self, sunday: Sunday, lineup: Lineup, board_member: Player
    ) -> None:
        services.confirm(sunday.game_day.pk, board_member)
        html = text(login(sunday.line[0]).get(TODAY))
        assert 'data-state="proximo"' in html
        assert "Time A" in html.split('data-state="proximo"')[1].split("</section>")[0]
        assert "Entra quem espera há mais tempo." in html

    def test_once_a_game_ends_the_waiting_line_names_the_next_one(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, after_kickoff: FixedClock
    ) -> None:
        services.confirm(sunday.game_day.pk, board_member)
        first = services.start_next_match(sunday.game_day.pk, board_member)
        me = LineupSpot.objects.filter(lineup=lineup, team__number=3).first().player
        assert 'data-state="proximo"' not in text(login(me).get(TODAY))  # a game is on court
        for scorer in members_of(first, Side.HOME)[:2]:
            services.add_goal(first.pk, board_member, side=Side.HOME, scorer=scorer.pk)
        html = text(login(me).get(TODAY))
        section = html.split('data-state="proximo"')[1].split("</section>")[0]
        assert "Próximo jogo · seu time" in section
        assert "Time A" in section
        assert "Time C" in section
        assert "Time B" in section.split("Esperando:")[1]

    def test_the_next_game_is_not_announced_once_the_game_is_over(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, after_kickoff: FixedClock
    ) -> None:
        services.confirm(sunday.game_day.pk, board_member)
        games.finish_game_day(sunday.game_day.pk, board_member)
        assert 'data-state="proximo"' not in text(login(sunday.line[0]).get(TODAY))

    def test_results_and_goals(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, clock: FixedClock
    ) -> None:
        services.rename_teams(
            sunday.game_day.pk, board_member, {1: "Azul", 2: "Verde", 3: "Branco", 4: "Preto"}
        )
        services.confirm(sunday.game_day.pk, board_member)
        match = services.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)
        scorer = LineupSpot.objects.filter(lineup=lineup, team__number=1).first().player
        clock.set(AFTER_KICKOFF)
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
        services.finish_match(match.pk, board_member)
        html = text(login(sunday.line[0]).get(TODAY))
        assert "1 x 0" in html
        assert scorer.name in html

    def test_result_of_the_day_stays_after_the_game(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, clock: FixedClock
    ) -> None:
        services.confirm(sunday.game_day.pk, board_member)
        match = services.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)
        scorer = LineupSpot.objects.filter(lineup=lineup, team__number=1).first().player
        clock.set(AFTER_KICKOFF)  # goals wait for the kickoff
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
        services.finish_match(match.pk, board_member)
        for moment in (
            datetime(2026, 10, 18, 12, 40, tzinfo=SP),
            datetime(2026, 10, 18, 23, 59, tzinfo=SP),
            datetime(2026, 10, 19, 7, 59, tzinfo=SP),
            datetime(2026, 10, 19, 8, 0, tzinfo=SP),  # the Bola de Ouro result is read all Monday
            datetime(2026, 10, 19, 23, 59, tzinfo=SP),
        ):
            clock.set(moment)
            html = text(login(sunday.line[0]).get(TODAY))
            assert "Domingo, 18/10" in html
            assert 'data-state="resultado"' in html
            assert "1 de 1 jogo encerrado, 1 gol." in html
            assert "1 x 0" in html
            assert "sem-times" not in html
        clock.set(datetime(2026, 10, 20, 0, 0, tzinfo=SP))
        assert "Domingo, 25/10" in text(login(sunday.line[0]).get(TODAY))

    def test_home_card_closes_the_sunday_just_played_after_the_game(
        self, sunday: Sunday, clock: FixedClock
    ) -> None:
        clock.set(datetime(2026, 10, 18, 12, 40, tzinfo=SP))
        client = login(sunday.line[0])
        html = text(client.get(reverse("games:home")))
        assert "Domingo encerrado" in html
        assert "Domingo, 18/10" in html
        assert "Acompanhar o domingo" not in html.split('<nav class="tabbar"', 1)[0]
        assert "Domingo, 18/10" in text(client.get(TODAY))

    def test_board_tabs_stay_on_the_sunday_of_the_day(
        self, sunday: Sunday, board_client: Client, clock: FixedClock
    ) -> None:
        clock.set(datetime(2026, 10, 18, 12, 40, tzinfo=SP))
        html = text(board_client.get(reverse("board:dashboard")))
        for name in ("game_day", "teams"):
            assert f'href="{reverse(f"board:{name}", kwargs={"day": DAY})}"' in html
        assert "18/10" in html
        assert "Domingo 25/10" not in html
        clock.set(datetime(2026, 10, 19, 8, 0, tzinfo=SP))
        html = text(board_client.get(reverse("board:dashboard")))
        assert f'href="{reverse("board:game_day", kwargs={"day": "2026-10-25"})}"' in html

    def test_no_result_banner_during_the_game(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, clock: FixedClock
    ) -> None:
        services.confirm(sunday.game_day.pk, board_member)
        services.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)
        clock.set(AFTER_KICKOFF)
        assert 'data-state="resultado"' not in text(login(sunday.line[0]).get(TODAY))

    def test_past_sundays(
        self, sunday: Sunday, lineup: Lineup, board_member: Player, clock: FixedClock
    ) -> None:
        services.confirm(sunday.game_day.pk, board_member)
        clock.advance(timedelta(days=7))
        client = login(sunday.line[0])
        html = text(client.get(TODAY))
        assert "Domingos anteriores" in html
        past = client.get(reverse("matchday:day", kwargs={"day": DAY}))
        assert "Time A" in text(past)

    @pytest.mark.parametrize("day", ["2026-10-25", "ontem"])
    def test_unknown_sunday_is_404(self, sunday: Sunday, day: str) -> None:
        response = login(sunday.line[0]).get(reverse("matchday:day", kwargs={"day": day}))
        assert response.status_code == 404

    def test_cancelled_sunday(self, sunday: Sunday, lineup: Lineup, board_member: Player) -> None:
        services.confirm(sunday.game_day.pk, board_member)
        games.cancel_game_day(sunday.game_day.pk, board_member, "Chuva")
        html = text(login(sunday.line[0]).get(TODAY))
        assert 'data-state="cancelado"' in html


class TestBoardTeams:
    def test_draw_from_the_screen(self, sunday: Sunday, board_client: Client) -> None:
        url = reverse("board:teams", kwargs={"day": DAY})
        assert 'data-lineup="none"' in text(board_client.get(url))
        response = board_client.post(reverse("board:draw_teams", kwargs={"day": DAY}), follow=True)
        html = text(response)
        assert "Times sorteados." in html
        assert 'data-lineup="draft"' in html
        assert html.count('class="spot__sum"') == 4
        assert Lineup.objects.get().spots.count() == 20

    def test_a_missing_choice_is_asked_for(
        self, sunday: Sunday, lineup: Lineup, board_client: Client
    ) -> None:
        url = reverse("board:assign_teams", kwargs={"day": DAY})
        mover = sunday.line[0]
        before = team_number(lineup, mover)
        response = board_client.post(url, {"modo": "mover", "unidade": f"u{mover.pk}"})
        assert response.status_code == 200
        assert "Escolha para qual time mover." in text(response)
        response = board_client.post(url, {"modo": "mover", "destino": "1"})
        assert "Escolha quem mover." in text(response)
        response = board_client.post(
            url, {"modo": "mover", "unidade": f"u{mover.pk}", "destino": "9"}
        )
        assert "Escolha para qual time mover." in text(response)
        assert team_number(lineup, mover) == before

    def test_list_changes_are_flagged(
        self, sunday: Sunday, lineup: Lineup, board_client: Client, board_member: Player
    ) -> None:
        games.leave_list(sunday.game_day.pk, sunday.line[0], board_actor=board_member)
        html = text(board_client.get(reverse("board:teams", kwargs={"day": DAY})))
        assert 'data-lineup="stale"' in html
        assert f"Saíram ou faltaram: {sunday.line[0].name}" in html
        response = board_client.post(
            reverse("board:confirm_teams", kwargs={"day": DAY}), follow=True
        )
        assert "A lista mudou depois do sorteio" in text(response)

    def test_a_newcomer_waits_for_a_team_while_others_are_adjusted(
        self,
        sunday: Sunday,
        lineup: Lineup,
        board_client: Client,
        board_member: Player,
        make_player,
    ) -> None:
        url = reverse("board:assign_teams", kwargs={"day": DAY})
        leaving, mover = sunday.line[0], sunday.line[1]
        games.leave_list(sunday.game_day.pk, leaving, board_actor=board_member)
        newcomer = make_player("Novato")
        games.join_list(sunday.game_day.pk, newcomer)
        target = 1 if team_number(lineup, mover) != 1 else 2
        # Someone who left is simply dropped; the newcomer stays unplaced until given a team.
        response = board_client.post(
            url, {"modo": "mover", "unidade": f"u{mover.pk}", "destino": str(target)}, follow=True
        )
        html = text(response)
        assert "foi para o Time" in html
        assert team_number(lineup, mover) == target
        assert not lineup.spots.filter(player=leaving).exists()
        assert "Entraram depois do sorteio" in html
        assert not lineup.spots.filter(player=newcomer).exists()

        response = board_client.post(
            url, {"modo": "mover", "unidade": f"u{newcomer.pk}", "destino": "1"}, follow=True
        )
        assert team_number(lineup, newcomer) == 1
        assert 'data-lineup="stale"' not in text(response)

    def test_rename_and_confirm(self, sunday: Sunday, lineup: Lineup, board_client: Client) -> None:
        names = {"t1": "Azul", "t2": "Verde", "t3": "Branco", "t4": "Preto"}
        response = board_client.post(
            reverse("board:rename_teams", kwargs={"day": DAY}), names, follow=True
        )
        assert "Nomes dos times salvos." in text(response)
        response = board_client.post(
            reverse("board:confirm_teams", kwargs={"day": DAY}), follow=True
        )
        html = text(response)
        assert 'data-lineup="confirmed"' in html
        response = board_client.post(
            reverse("board:unconfirm_teams", kwargs={"day": DAY}), follow=True
        )
        assert 'data-lineup="draft"' in text(response)

    def test_duplicate_names_are_rejected(
        self, sunday: Sunday, lineup: Lineup, board_client: Client
    ) -> None:
        names = {"t1": "Azul", "t2": "AZUL", "t3": "Branco", "t4": "Preto"}
        response = board_client.post(
            reverse("board:rename_teams", kwargs={"day": DAY}), names, follow=True
        )
        assert "nomes diferentes" in text(response)

    def test_blank_name_rerenders_the_form(
        self, sunday: Sunday, lineup: Lineup, board_client: Client
    ) -> None:
        names = {"t1": "", "t2": "B", "t3": "C", "t4": "D"}
        response = board_client.post(reverse("board:rename_teams", kwargs={"day": DAY}), names)
        assert response.status_code == 200
        assert "obrigatório" in text(response)

    def test_actions_before_the_draw(self, sunday: Sunday, board_client: Client) -> None:
        for name in ("assign_teams", "rename_teams", "confirm_teams"):
            response = board_client.post(reverse(f"board:{name}", kwargs={"day": DAY}), follow=True)
            assert "Sorteie os times primeiro." in text(response)

    def test_cancelled_sunday(
        self, sunday: Sunday, board_client: Client, board_member: Player
    ) -> None:
        games.cancel_game_day(sunday.game_day.pk, board_member, "Chuva")
        html = text(board_client.get(reverse("board:teams", kwargs={"day": DAY})))
        assert "Domingo cancelado" in html
        response = board_client.post(reverse("board:draw_teams", kwargs={"day": DAY}), follow=True)
        assert "Este domingo foi cancelado." in text(response)


@pytest.fixture
def match(sunday: Sunday, lineup: Lineup, board_member: Player, after_kickoff: FixedClock) -> Match:
    return services.add_match(
        sunday.game_day.pk,
        board_member,
        home_team=1,
        away_team=2,
        home_goalkeeper=sunday.goalkeepers[0].pk,
        away_goalkeeper=sunday.goalkeepers[1].pk,
    )


def side_players(lineup: Lineup, number: int) -> list[Player]:
    return [s.player for s in LineupSpot.objects.filter(lineup=lineup, team__number=number)]


class TestMatchScreen:
    def test_score_a_match_to_the_end(
        self, match: Match, lineup: Lineup, board_client: Client
    ) -> None:
        page = text(board_client.get(reverse("board:match", kwargs={"match_id": match.pk})))
        assert "data-match-timer" in page
        assert "Gol contra (do Time B)" in page

        home = side_players(lineup, 1)
        url = reverse("board:add_goal", kwargs={"match_id": match.pk, "side": "home"})
        response = board_client.post(url, {"scorer": home[0].pk, "assist": home[1].pk}, follow=True)
        assert "Gol do Time A!" in text(response)
        response = board_client.post(url, {"scorer": home[2].pk, "assist": ""}, follow=True)
        html = text(response)
        assert 'data-match-state="encerrada"' in html
        assert "data-match-timer" not in html
        assert f"(assistência de {home[1].name})" in html
        assert "Reabrir partida" in html

    def test_own_goal(self, match: Match, board_client: Client) -> None:
        url = reverse("board:add_goal", kwargs={"match_id": match.pk, "side": "away"})
        board_client.post(url, {"scorer": "contra"})
        goal = Goal.objects.get()
        assert goal.own_goal
        assert goal.team == match.away_team

    @pytest.mark.parametrize(
        ("data", "error"),
        [
            ({"scorer": ""}, "Este campo é obrigatório."),
            ({"scorer": "contra", "assist": "SAME"}, "Gol contra não tem assistência."),
            ({"scorer": "SAME", "assist": "SAME"}, "não pode ser também a assistência"),
        ],
    )
    def test_invalid_goal(
        self, match: Match, lineup: Lineup, board_client: Client, data: dict, error: str
    ) -> None:
        someone = str(side_players(lineup, 1)[0].pk)
        data = {key: someone if value == "SAME" else value for key, value in data.items()}
        url = reverse("board:add_goal", kwargs={"match_id": match.pk, "side": "home"})
        response = board_client.post(url, data, follow=True)
        assert error in text(response)
        assert not Goal.objects.exists()

    def test_substitute_from_another_team_can_score(
        self, match: Match, lineup: Lineup, board_client: Client
    ) -> None:
        substitute = side_players(lineup, 3)[0]
        url = reverse("board:add_goal", kwargs={"match_id": match.pk, "side": "home"})
        assert "Gol do Time A!" in text(
            board_client.post(url, {"scorer": substitute.pk}, follow=True)
        )
        goal = Goal.objects.get()
        assert (goal.team, goal.scorer) == (match.home_team, substitute)

    def test_player_off_the_list_is_not_a_choice(
        self, match: Match, board_client: Client, make_player
    ) -> None:
        outsider = make_player("De fora")
        url = reverse("board:add_goal", kwargs={"match_id": match.pk, "side": "home"})
        response = board_client.post(url, {"scorer": outsider.pk}, follow=True)
        assert "Faça uma escolha válida" in text(response)
        assert not Goal.objects.exists()

    def test_unknown_side_is_404(self, match: Match, board_client: Client) -> None:
        url = reverse("board:add_goal", kwargs={"match_id": match.pk, "side": "meio"})
        assert board_client.post(url, {"scorer": "1"}).status_code == 404

    def test_delete_goal_finish_and_reopen(
        self, match: Match, lineup: Lineup, board_client: Client, board_member: Player
    ) -> None:
        scorer = side_players(lineup, 1)[0]
        goal = services.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
        delete_url = reverse("board:delete_goal", kwargs={"match_id": match.pk, "goal_id": goal.pk})
        finish = reverse("board:finish_match", kwargs={"match_id": match.pk})
        reopen = reverse("board:reopen_match", kwargs={"match_id": match.pk})

        assert "Partida encerrada." in text(board_client.post(finish, follow=True))
        assert "Partida reaberta." in text(board_client.post(reopen, follow=True))
        assert "Gol apagado." in text(board_client.post(delete_url, follow=True))
        assert not Goal.objects.exists()

    def test_set_goalkeepers(self, match: Match, sunday: Sunday, board_client: Client) -> None:
        url = reverse("board:set_goalkeepers", kwargs={"match_id": match.pk})
        data = {
            "home_goalkeeper": str(sunday.goalkeepers[1].pk),
            "away_goalkeeper": str(sunday.goalkeepers[0].pk),
        }
        assert "Goleiros salvos." in text(board_client.post(url, data, follow=True))
        match.refresh_from_db()
        assert match.home_goalkeeper == sunday.goalkeepers[1]
        response = board_client.post(url, {"home_goalkeeper": "999999"}, follow=True)
        assert "Faça uma escolha válida" in text(response)

    def test_editing_one_goalkeeper_keeps_the_other_after_it_leaves_the_pool(
        self,
        match: Match,
        sunday: Sunday,
        board_client: Client,
        board_member: Player,
        clock: FixedClock,
    ) -> None:
        gone = sunday.goalkeepers[0]
        clock.set(AFTER_KICKOFF)
        games.mark_attendance(sunday.game_day.pk, gone, Attendance.FALTOU, board_member)
        page = board_client.get(reverse("board:match", kwargs={"match_id": match.pk}))
        form = page.context["goalkeepers_form"]
        assert form["home_goalkeeper"].value() == gone.pk
        assert str(gone.pk) in dict(form.fields["home_goalkeeper"].choices)
        url = reverse("board:set_goalkeepers", kwargs={"match_id": match.pk})
        data = {"home_goalkeeper": str(gone.pk), "away_goalkeeper": ""}
        assert "Goleiros salvos." in text(board_client.post(url, data, follow=True))
        match.refresh_from_db()
        assert (match.home_goalkeeper, match.away_goalkeeper) == (gone, None)

    def test_delete_match(
        self, match: Match, lineup: Lineup, board_client: Client, board_member: Player
    ) -> None:
        url = reverse("board:delete_match", kwargs={"match_id": match.pk})
        services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=side_players(lineup, 1)[0].pk
        )
        response = board_client.post(url, follow=True)
        assert "Partida apagada." in text(response)
        assert not Goal.objects.exists()
        assert response.redirect_chain[-1][0] == reverse("board:matches", kwargs={"day": DAY})
