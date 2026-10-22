"""The football is informal, so the diretoria can fix the teams, rosters, matches and goals at
any time while the data stays consistent for the ranking."""

import random
from collections.abc import Callable

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from matchday import services
from matchday.domain.score import Score, Side
from matchday.models import Goal, Lineup, LineupSpot, Match
from matchday.services import MatchdayError
from tests.conftest import Sunday

pytestmark = pytest.mark.django_db


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday]) -> Sunday:
    return fill_sunday()


@pytest.fixture
def lineup(sunday: Sunday, board_member: Player) -> Lineup:
    lineup = services.draw(sunday.game_day.pk, board_member, random.Random(3))
    services.confirm(sunday.game_day.pk, board_member)
    return lineup


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


def team(lineup: Lineup, number: int) -> list[Player]:
    spots = LineupSpot.objects.filter(lineup=lineup, team__number=number).select_related("player")
    return [spot.player for spot in spots.order_by("player__name")]


def played(match: Match) -> dict[int, set[Player]]:
    """Team number -> who played the match for it."""
    result: dict[int, set[Player]] = {}
    for row in match.players.select_related("player", "team"):
        result.setdefault(row.team.number, set()).add(row.player)
    return result


def assignments(lineup: Lineup, **moves: int) -> dict[int, int | None]:
    current = {
        spot.player_id: spot.team.number if spot.team else None
        for spot in lineup.spots.select_related("team")
    }
    return current | {int(pk.removeprefix("p")): number for pk, number in moves.items()}


class TestMatchRoster:
    def test_a_new_match_is_played_by_the_teams_without_the_keepers(
        self, match: Match, lineup: Lineup
    ) -> None:
        assert played(match) == {1: set(team(lineup, 1)), 2: set(team(lineup, 2))}

    def test_a_line_player_drawn_to_goal_keeps_goal_instead_of_playing_the_line(
        self, fill_sunday: Callable[..., Sunday], board_member: Player
    ) -> None:
        sunday = fill_sunday(line=21, goalkeepers=1)
        lineup = services.draw(sunday.game_day.pk, board_member, random.Random(3))
        keeper = lineup.spots.get(team=None).player
        match = services.add_match(
            sunday.game_day.pk, board_member, home_team=1, away_team=2, home_goalkeeper=keeper.pk
        )
        assert all(keeper not in players for players in played(match).values())
        with pytest.raises(MatchdayError, match="lista do domingo"):
            services.set_roster(match.pk, board_member, {keeper.pk: Side.AWAY})
        # Off goal duty they can play the line, and keeping goal takes them out again.
        services.set_goalkeepers(match.pk, board_member, home=None, away=None)
        services.set_roster(match.pk, board_member, {keeper.pk: Side.AWAY})
        assert played(match) == {2: {keeper}}
        services.set_goalkeepers(match.pk, board_member, home=keeper.pk, away=None)
        assert played(match) == {}

    def test_adjusting_the_teams_updates_matches_not_yet_played(
        self, match: Match, lineup: Lineup, sunday: Sunday, board_member: Player
    ) -> None:
        mover, other = team(lineup, 1)[0], team(lineup, 2)[0]
        moves = {f"p{mover.pk}": 2, f"p{other.pk}": 1}
        services.assign(sunday.game_day.pk, board_member, assignments(lineup, **moves))
        assert played(match) == {1: {*team(lineup, 1)}, 2: {*team(lineup, 2)}}
        assert mover in played(match)[2]
        assert other in played(match)[1]

    def test_a_match_with_goals_keeps_who_played_it(
        self, match: Match, lineup: Lineup, sunday: Sunday, board_member: Player
    ) -> None:
        scorer = team(lineup, 1)[0]
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
        before = played(match)
        services.assign(
            sunday.game_day.pk, board_member, assignments(lineup, **{f"p{scorer.pk}": 3})
        )
        assert played(match) == before
        goal = Goal.objects.get()
        assert (goal.scorer, goal.team) == (scorer, match.home_team)

    def test_a_finished_match_keeps_who_played_it(
        self, match: Match, lineup: Lineup, sunday: Sunday, board_member: Player
    ) -> None:
        services.finish_match(match.pk, board_member)
        before = played(match)
        mover = team(lineup, 2)[0]
        services.assign(
            sunday.game_day.pk, board_member, assignments(lineup, **{f"p{mover.pk}": 1})
        )
        assert played(match) == before

    def test_a_redraw_updates_matches_not_yet_played(
        self, match: Match, lineup: Lineup, sunday: Sunday, board_member: Player
    ) -> None:
        services.unconfirm(sunday.game_day.pk, board_member)
        services.draw(sunday.game_day.pk, board_member, random.Random(77))
        assert played(match) == {1: {*team(lineup, 1)}, 2: {*team(lineup, 2)}}

    def test_substitute_for_one_match(
        self, match: Match, lineup: Lineup, board_member: Player
    ) -> None:
        sitting, cover = team(lineup, 1)[0], team(lineup, 3)[0]
        roster = {p.pk: Side.HOME for p in team(lineup, 1)} | {
            p.pk: Side.AWAY for p in team(lineup, 2)
        }
        roster[sitting.pk] = None
        roster[cover.pk] = Side.HOME
        services.set_roster(match.pk, board_member, roster)
        assert cover in played(match)[1]
        assert sitting not in played(match)[1]
        # The Sunday's lineup is untouched.
        assert sitting in team(lineup, 1)
        assert cover in team(lineup, 3)
        match.refresh_from_db()
        assert not match.roster_follows_lineup

    def test_a_substituted_match_stops_following_the_lineup(
        self, match: Match, lineup: Lineup, sunday: Sunday, board_member: Player
    ) -> None:
        roster = {p.pk: Side.HOME for p in team(lineup, 1)}
        services.set_roster(match.pk, board_member, roster)
        mover = team(lineup, 2)[0]
        services.assign(
            sunday.game_day.pk, board_member, assignments(lineup, **{f"p{mover.pk}": 1})
        )
        assert played(match) == {1: set(team(lineup, 1)) - {mover}}

    def test_roster_rules(
        self, match: Match, sunday: Sunday, board_member: Player, make_player
    ) -> None:
        outsider = make_player("De fora")
        with pytest.raises(MatchdayError, match="lista do domingo"):
            services.set_roster(match.pk, board_member, {outsider.pk: Side.HOME})
        with pytest.raises(MatchdayError, match="lista do domingo"):
            services.set_roster(match.pk, board_member, {sunday.goalkeepers[0].pk: Side.HOME})
        with pytest.raises(PermissionDenied):
            services.set_roster(match.pk, sunday.line[0], {})

    def test_the_scorer_and_assist_played_for_the_team_they_scored_for(
        self, match: Match, lineup: Lineup, board_member: Player
    ) -> None:
        cover, assister = team(lineup, 3)[0], team(lineup, 4)[0]
        services.add_goal(
            match.pk, board_member, side=Side.AWAY, scorer=cover.pk, assist=assister.pk
        )
        assert {cover, assister} <= played(match)[2]
        # Someone already playing for the other side stays where they were.
        home_player = team(lineup, 1)[0]
        services.add_goal(match.pk, board_member, side=Side.AWAY, scorer=home_player.pk)
        assert home_player in played(match)[1]
        assert home_player not in played(match)[2]


class TestEditGoals:
    def test_fix_the_scorer_and_assist(
        self, match: Match, lineup: Lineup, board_member: Player
    ) -> None:
        home = team(lineup, 1)
        goal = services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=home[0].pk, assist=home[1].pk
        )
        services.edit_goal(goal.pk, board_member, side=Side.HOME, scorer=home[2].pk, assist=None)
        goal.refresh_from_db()
        assert (goal.scorer, goal.assist, goal.team) == (home[2], None, match.home_team)

    def test_credit_the_goal_to_the_other_team(
        self, match: Match, lineup: Lineup, board_member: Player
    ) -> None:
        away = team(lineup, 2)
        goal = services.add_goal(match.pk, board_member, side=Side.HOME, scorer=away[0].pk)
        services.edit_goal(goal.pk, board_member, side=Side.AWAY, scorer=away[0].pk)
        goal.refresh_from_db()
        assert goal.team == match.away_team
        assert services.score_of(match) == Score(0, 1)

    def test_turn_a_goal_into_an_own_goal_and_back(
        self, match: Match, lineup: Lineup, board_member: Player
    ) -> None:
        home = team(lineup, 1)
        goal = services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=home[0].pk, assist=home[1].pk
        )
        services.edit_goal(goal.pk, board_member, side=Side.HOME, scorer=None, own_goal=True)
        goal.refresh_from_db()
        assert (goal.own_goal, goal.scorer, goal.assist) == (True, None, None)
        services.edit_goal(goal.pk, board_member, side=Side.HOME, scorer=home[2].pk)
        goal.refresh_from_db()
        assert (goal.own_goal, goal.scorer) == (False, home[2])

    def test_a_finished_match_can_be_corrected_in_place(
        self, match: Match, lineup: Lineup, board_member: Player
    ) -> None:
        home, away = team(lineup, 1), team(lineup, 2)
        first = services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[0].pk)
        services.add_goal(match.pk, board_member, side=Side.AWAY, scorer=away[0].pk)
        services.finish_match(match.pk, board_member)
        services.edit_goal(first.pk, board_member, side=Side.HOME, scorer=home[1].pk)
        late = services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[2].pk)
        match.refresh_from_db()
        assert match.is_finished
        assert services.score_of(match) == Score(2, 1)
        services.delete_goal(late.pk, board_member)
        assert services.score_of(match) == Score(1, 1)
        assert match.is_finished

    def test_moving_a_goal_that_decides_the_match_finishes_it(
        self, match: Match, lineup: Lineup, board_member: Player
    ) -> None:
        home, away = team(lineup, 1), team(lineup, 2)
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[0].pk)
        away_goal = services.add_goal(match.pk, board_member, side=Side.AWAY, scorer=away[0].pk)
        match.refresh_from_db()
        assert not match.is_finished
        services.edit_goal(away_goal.pk, board_member, side=Side.HOME, scorer=home[1].pk)
        match.refresh_from_db()
        assert services.score_of(match) == Score(2, 0)
        assert match.is_finished

    def test_the_two_goal_limit_still_holds(
        self, match: Match, lineup: Lineup, board_member: Player
    ) -> None:
        home, away = team(lineup, 1), team(lineup, 2)
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[0].pk)
        services.add_goal(match.pk, board_member, side=Side.AWAY, scorer=away[0].pk)
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[1].pk)
        first = match.goals.order_by("pk").first()
        # 2 x 1: moving the away goal to home would make 3 x 0.
        away_goal = match.goals.get(team=match.away_team)
        with pytest.raises(MatchdayError, match="já acabou"):
            services.edit_goal(away_goal.pk, board_member, side=Side.HOME, scorer=away[0].pk)
        # Moving one of the two home goals keeps the total and gives 1 x 2.
        services.edit_goal(first.pk, board_member, side=Side.AWAY, scorer=home[0].pk)
        assert services.score_of(match) == Score(1, 2)

    def test_scorer_and_assist_are_validated(
        self, match: Match, lineup: Lineup, board_member: Player, make_player
    ) -> None:
        home = team(lineup, 1)
        goal = services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[0].pk)
        outsider = make_player("De fora")
        with pytest.raises(MatchdayError, match="autor do gol"):
            services.edit_goal(goal.pk, board_member, side=Side.HOME, scorer=outsider.pk)
        with pytest.raises(MatchdayError, match="assistência"):
            services.edit_goal(
                goal.pk, board_member, side=Side.HOME, scorer=home[0].pk, assist=outsider.pk
            )
        with pytest.raises(MatchdayError, match="também"):
            services.edit_goal(
                goal.pk, board_member, side=Side.HOME, scorer=home[0].pk, assist=home[0].pk
            )
        with pytest.raises(MatchdayError, match="Gol contra"):
            services.edit_goal(
                goal.pk, board_member, side=Side.HOME, scorer=home[0].pk, own_goal=True
            )
        goal.refresh_from_db()
        assert goal.scorer == home[0]

    def test_only_the_board_edits_goals(self, match: Match, lineup: Lineup, board_member) -> None:
        home = team(lineup, 1)
        goal = services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[0].pk)
        with pytest.raises(PermissionDenied):
            services.edit_goal(goal.pk, home[0], side=Side.HOME, scorer=home[1].pk)


class TestEditMatches:
    def test_replace_a_team_and_its_goals_follow_the_new_team(
        self, match: Match, lineup: Lineup, board_member: Player
    ) -> None:
        home = team(lineup, 1)
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[0].pk)
        services.edit_match(match.pk, board_member, home_team=3, away_team=2)
        match.refresh_from_db()
        goal = Goal.objects.get()
        assert goal.team == match.home_team
        assert match.home_team.number == 3
        assert services.score_of(match) == Score(1, 0)
        assert played(match)[3] >= {*team(lineup, 3)} - {home[0]}
        assert home[0] in played(match)[3]
        assert 1 not in played(match)

    def test_swapping_the_teams_keeps_each_teams_goals(
        self, match: Match, lineup: Lineup, board_member: Player
    ) -> None:
        home = team(lineup, 1)
        services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[0].pk)
        before = played(match)
        services.edit_match(match.pk, board_member, home_team=2, away_team=1)
        match.refresh_from_db()
        assert match.away_team.number == 1
        assert Goal.objects.get().team == match.away_team
        assert services.score_of(match) == Score(0, 1)
        assert played(match) == before

    def test_both_teams_replaced(self, match: Match, lineup: Lineup, board_member: Player) -> None:
        away = team(lineup, 2)
        services.add_goal(match.pk, board_member, side=Side.AWAY, scorer=away[0].pk)
        services.edit_match(match.pk, board_member, home_team=3, away_team=4)
        match.refresh_from_db()
        assert Goal.objects.get().team == match.away_team
        assert match.away_team.number == 4

    @pytest.mark.parametrize(
        ("home", "away", "error"),
        [(1, 1, "dois times diferentes"), (1, 9, "Time inválido")],
    )
    def test_invalid_teams(
        self, match: Match, board_member: Player, home: int, away: int, error: str
    ) -> None:
        with pytest.raises(MatchdayError, match=error):
            services.edit_match(match.pk, board_member, home_team=home, away_team=away)

    def test_move_a_match_in_the_table(
        self, sunday: Sunday, match: Match, board_member: Player
    ) -> None:
        second = services.add_match(sunday.game_day.pk, board_member, home_team=3, away_team=4)
        third = services.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=3)
        services.move_match(third.pk, board_member, earlier=True)
        order = list(Match.objects.order_by("order").values_list("pk", flat=True))
        assert order == [match.pk, third.pk, second.pk]
        services.move_match(match.pk, board_member, earlier=False)
        order = list(Match.objects.order_by("order").values_list("pk", flat=True))
        assert order == [third.pk, match.pk, second.pk]
        with pytest.raises(MatchdayError, match="primeira"):
            services.move_match(third.pk, board_member, earlier=True)
        with pytest.raises(MatchdayError, match="última"):
            services.move_match(second.pk, board_member, earlier=False)

    def test_only_the_board_edits_matches(self, match: Match, sunday: Sunday) -> None:
        with pytest.raises(PermissionDenied):
            services.edit_match(match.pk, sunday.line[0], home_team=3, away_team=4)
        with pytest.raises(PermissionDenied):
            services.move_match(match.pk, sunday.line[0], earlier=True)
        with pytest.raises(PermissionDenied):
            services.delete_match(match.pk, sunday.line[0])


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


def page(response) -> str:
    return response.content.decode()


class TestScreens:
    def test_edit_a_goal(
        self, match: Match, lineup: Lineup, board_client: Client, board_member: Player
    ) -> None:
        home, away = team(lineup, 1), team(lineup, 2)
        goal = services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=home[0].pk, assist=home[1].pk
        )
        url = reverse("board:edit_goal", kwargs={"match_id": match.pk, "goal_id": goal.pk})
        form = board_client.get(url).context["form"]
        assert form["side"].value() == "home"
        assert form["scorer"].value() == str(home[0].pk)
        assert form["assist"].value() == str(home[1].pk)

        response = board_client.post(
            url, {"side": "away", "scorer": str(away[0].pk), "assist": ""}, follow=True
        )
        assert "Gol corrigido." in page(response)
        goal.refresh_from_db()
        assert (goal.team, goal.scorer, goal.assist) == (match.away_team, away[0], None)

    def test_edit_a_goal_shows_the_error_and_keeps_the_form(
        self, match: Match, lineup: Lineup, board_client: Client, board_member: Player
    ) -> None:
        home = team(lineup, 1)
        goal = services.add_goal(match.pk, board_member, side=Side.HOME, scorer=home[0].pk)
        url = reverse("board:edit_goal", kwargs={"match_id": match.pk, "goal_id": goal.pk})
        data = {"side": "home", "scorer": str(home[1].pk), "assist": str(home[1].pk)}
        response = board_client.post(url, data)
        assert response.status_code == 200
        assert "não pode ser também a assistência" in page(response)

    def test_edit_a_goal_of_another_match_is_404(
        self, match: Match, lineup: Lineup, board_client: Client, board_member: Player, sunday
    ) -> None:
        other = services.add_match(sunday.game_day.pk, board_member, home_team=3, away_team=4)
        goal = services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=team(lineup, 1)[0].pk
        )
        url = reverse("board:edit_goal", kwargs={"match_id": other.pk, "goal_id": goal.pk})
        assert board_client.get(url).status_code == 404

    def test_set_the_roster(self, match: Match, lineup: Lineup, board_client: Client) -> None:
        sitting, cover = team(lineup, 1)[0], team(lineup, 3)[0]
        url = reverse("board:set_roster", kwargs={"match_id": match.pk})
        form = board_client.get(reverse("board:match", kwargs={"match_id": match.pk})).context[
            "roster_form"
        ]
        data = {name: field.initial for name, field in form.fields.items()}
        data[form.field_name(sitting)] = ""
        data[form.field_name(cover)] = "home"
        response = board_client.post(url, data, follow=True)
        assert "Escalação da partida salva." in page(response)
        assert sitting not in played(match)[1]
        assert cover in played(match)[1]

    def test_set_the_roster_rejects_an_unknown_side(
        self, match: Match, lineup: Lineup, board_client: Client
    ) -> None:
        url = reverse("board:set_roster", kwargs={"match_id": match.pk})
        someone = team(lineup, 1)[0]
        response = board_client.post(url, {f"p{someone.pk}": "meio"}, follow=True)
        assert "Faça uma escolha válida" in page(response)

    def test_change_the_teams_of_a_match(self, match: Match, board_client: Client) -> None:
        url = reverse("board:edit_match", kwargs={"match_id": match.pk})
        response = board_client.post(url, {"home_team": "3", "away_team": "4"}, follow=True)
        assert "Times da partida salvos." in page(response)
        match.refresh_from_db()
        assert (match.home_team.number, match.away_team.number) == (3, 4)
        response = board_client.post(url, {"home_team": "3", "away_team": "3"}, follow=True)
        assert "Escolha dois times diferentes." in page(response)

    def test_move_matches_in_the_table(
        self, sunday: Sunday, match: Match, board_client: Client, board_member: Player
    ) -> None:
        second = services.add_match(sunday.game_day.pk, board_member, home_team=3, away_team=4)
        up = reverse("board:move_match_up", kwargs={"match_id": second.pk})
        assert "Partida movida na tabela." in page(board_client.post(up, follow=True))
        assert list(Match.objects.order_by("order")) == [second, match]
        response = board_client.post(up, follow=True)
        assert "A partida já é a primeira." in page(response)

    def test_players_cannot_edit(
        self, match: Match, lineup: Lineup, sunday: Sunday, board_member: Player
    ) -> None:
        client = Client()
        client.force_login(sunday.line[0])
        goal = services.add_goal(
            match.pk, board_member, side=Side.HOME, scorer=team(lineup, 1)[0].pk
        )
        for name, kwargs in (
            ("edit_goal", {"match_id": match.pk, "goal_id": goal.pk}),
            ("set_roster", {"match_id": match.pk}),
            ("edit_match", {"match_id": match.pk}),
            ("move_match_up", {"match_id": match.pk}),
            ("move_match_down", {"match_id": match.pk}),
        ):
            assert client.post(reverse(f"board:{name}", kwargs=kwargs)).status_code == 403
