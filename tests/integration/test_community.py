import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError

from accounts.models import Player
from community import profile as profiles
from community import seasons, selectors, services
from community.domain import stats, voting
from community.domain.ranking import Metric
from community.models import Ballot, PlaceTally, Season
from community.services import CommunityError
from core.clock import FixedClock
from games.models import Attendance, GameDay
from matchday import services as matchday
from matchday.domain.score import Side
from matchday.models import LineupSpot, Match
from tests.conftest import SP, Sunday, end_game, start_game

pytestmark = pytest.mark.django_db

KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)
AFTER_GAME = datetime(2026, 10, 18, 11, 30, tzinfo=SP)
MONDAY_MORNING = datetime(2026, 10, 19, 9, 0, tzinfo=SP)
MONDAY_LIST_OPENS = datetime(2026, 10, 19, 8, 0, tzinfo=SP)


@dataclass
class Played:
    sunday: Sunday
    board: Player
    first: Match  # team 1 beat team 2, 2 x 0 (A scored twice, B assisted once)
    second: Match  # team 3 x team 4 ended 1 x 1

    @property
    def game_day(self) -> GameDay:
        return self.sunday.game_day

    def team(self, number: int) -> list[Player]:
        # A stable order: the fixture and the tests must agree on who `team(n)[0]` is.
        spots = (
            LineupSpot.objects.filter(lineup__game_day=self.game_day, team__number=number)
            .select_related("player")
            .order_by("pk")
        )
        return [spot.player for spot in spots]


@pytest.fixture
def played(fill_sunday: Callable[..., Sunday], board_member: Player, clock: FixedClock) -> Played:
    sunday = fill_sunday()
    day_id = sunday.game_day.pk
    matchday.draw(day_id, board_member, random.Random(3))
    matchday.confirm(day_id, board_member)
    clock.set(KICKOFF)
    start_game(sunday.game_day)
    first = matchday.add_match(day_id, board_member, home_team=1, away_team=2)
    second = matchday.add_match(day_id, board_member, home_team=3, away_team=4)
    out = Played(sunday, board_member, first, second)
    one, two, three, four = (out.team(n) for n in (1, 2, 3, 4))
    matchday.add_goal(first.pk, board_member, side=Side.HOME, scorer=one[0].pk, assist=one[1].pk)
    matchday.add_goal(first.pk, board_member, side=Side.HOME, scorer=one[0].pk)
    matchday.add_goal(second.pk, board_member, side=Side.HOME, scorer=three[0].pk)
    matchday.add_goal(second.pk, board_member, side=Side.AWAY, scorer=four[0].pk, assist=two[0].pk)
    matchday.finish_match(second.pk, board_member)
    clock.set(AFTER_GAME)
    end_game(sunday.game_day)
    sunday.game_day.refresh_from_db()
    return out


def mark_absent(played: Played, player: Player) -> Player:
    entry = player.list_entries.get(game_day=played.game_day)
    entry.attendance = Attendance.FALTOU
    entry.save()
    return player


class TestVotingWindow:
    def test_opens_only_when_the_sunday_is_finished(
        self, fill_sunday, board_member: Player, clock: FixedClock
    ) -> None:
        sunday = fill_sunday()
        matchday.draw(sunday.game_day.pk, board_member, random.Random(3))
        assert selectors.window_of(sunday.game_day).phase == voting.Phase.NONE
        matchday.confirm(sunday.game_day.pk, board_member)
        clock.set(KICKOFF)
        start_game(sunday.game_day)
        match = matchday.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)
        assert selectors.window_of(sunday.game_day).phase == voting.Phase.WAITING
        matchday.finish_match(match.pk, board_member)
        assert selectors.window_of(sunday.game_day).phase == voting.Phase.WAITING
        clock.set(datetime(2026, 10, 18, 12, 0, tzinfo=SP))
        assert selectors.window_of(sunday.game_day).phase == voting.Phase.WAITING
        end_game(sunday.game_day)
        sunday.game_day.refresh_from_db()
        assert selectors.window_of(sunday.game_day).phase == voting.Phase.OPEN
        clock.set(MONDAY_LIST_OPENS)
        assert selectors.window_of(sunday.game_day).phase == voting.Phase.CLOSED

    def test_correcting_a_match_does_not_undo_the_sunday_end(
        self, played: Played, clock: FixedClock
    ) -> None:
        matchday.reopen_match(played.first.pk, played.board)
        assert selectors.window_of(played.game_day).phase == voting.Phase.OPEN

    def test_cancelled_sunday_has_no_vote(self, played: Played) -> None:
        from games import services as games

        games.cancel_game_day(played.game_day.pk, played.board, "chuva")
        played.game_day.refresh_from_db()
        assert selectors.window_of(played.game_day).phase == voting.Phase.NONE


class TestCastVote:
    def vote(self, played: Played, voter: Player, *picks: Player) -> None:
        first, second, third = (p.pk for p in picks)
        services.cast_vote(played.game_day.pk, voter, first=first, second=second, third=third)

    def test_votes_become_anonymous_counters(self, played: Played) -> None:
        voter, a, b, c = played.sunday.line[:4]
        self.vote(played, voter, a, b, c)
        assert Ballot.objects.filter(game_day=played.game_day, voter=voter).exists()
        tally = {(t.nominee_id, t.place): t.votes for t in PlaceTally.objects.all()}
        assert tally == {(a.pk, 1): 1, (b.pk, 2): 1, (c.pk, 3): 1}
        # Nothing ties the three picks to the voter.
        assert {f.name for f in PlaceTally._meta.get_fields()}.isdisjoint({"voter", "created_at"})

    def test_a_vote_is_final(self, played: Played) -> None:
        voter, a, b, c = played.sunday.line[:4]
        self.vote(played, voter, a, b, c)
        with pytest.raises(CommunityError, match="já votou"):
            self.vote(played, voter, b, c, a)
        assert PlaceTally.objects.count() == 3

    def test_goalkeepers_vote_and_receive_votes(self, played: Played) -> None:
        keeper = played.sunday.goalkeepers[0]
        a, b = played.sunday.line[:2]
        self.vote(played, keeper, a, b, played.sunday.goalkeepers[1])
        self.vote(played, a, keeper, b, played.sunday.line[2])
        assert PlaceTally.objects.filter(nominee=keeper, place=1).get().votes == 1

    @pytest.mark.parametrize("picks", ["self", "repeat", "missing"])
    def test_invalid_ballots_leave_no_trace(self, played: Played, picks: str) -> None:
        voter, a, b = played.sunday.line[:3]
        args = {
            "self": (voter.pk, a.pk, b.pk),
            "repeat": (a.pk, a.pk, b.pk),
            "missing": (a.pk, b.pk, None),
        }[picks]
        with pytest.raises(CommunityError):
            services.cast_vote(
                played.game_day.pk, voter, first=args[0], second=args[1], third=args[2]
            )
        assert not Ballot.objects.exists()
        assert not PlaceTally.objects.exists()

    def test_only_who_played_votes_and_is_voted_for(
        self, played: Played, make_player: Callable[..., Player]
    ) -> None:
        outsider = make_player("De fora")
        a, b, c = played.sunday.line[:3]
        with pytest.raises(CommunityError, match="jogou"):
            self.vote(played, outsider, a, b, c)
        with pytest.raises(CommunityError, match="jogou"):
            self.vote(played, a, outsider, b, c)

    def test_who_was_marked_absent_does_not_vote_or_get_votes(self, played: Played) -> None:
        absent = mark_absent(played, played.team(4)[1])
        voter, a, b = played.sunday.line[:3]
        with pytest.raises(CommunityError, match="jogou"):
            self.vote(played, absent, voter, a, b)
        with pytest.raises(CommunityError, match="jogou"):
            self.vote(played, voter, absent, a, b)

    def test_not_before_the_sunday_is_finished(self, played: Played, clock: FixedClock) -> None:
        GameDay.objects.filter(pk=played.game_day.pk).update(finished_at=None, finished_by=None)
        voter, a, b, c = played.sunday.line[:4]
        with pytest.raises(CommunityError, match="ainda não abriu"):
            self.vote(played, voter, a, b, c)

    def test_not_once_the_list_opens_on_monday(self, played: Played, clock: FixedClock) -> None:
        clock.set(MONDAY_LIST_OPENS)
        voter, a, b, c = played.sunday.line[:4]
        with pytest.raises(CommunityError, match="fechou"):
            self.vote(played, voter, a, b, c)

    def test_database_blocks_a_second_ballot(self, played: Played) -> None:
        voter = played.sunday.line[0]
        Ballot.objects.create(game_day=played.game_day, voter=voter)
        with pytest.raises(IntegrityError):
            Ballot.objects.create(game_day=played.game_day, voter=voter)


class TestResults:
    def cast_all(self, played: Played, winner: Player, runner_up: Player) -> None:
        others = [p for p in played.sunday.line if p not in (winner, runner_up)]
        for voter in others[:6]:
            services.cast_vote(
                played.game_day.pk, voter, first=winner.pk, second=runner_up.pk, third=others[7].pk
            )

    def test_results_stay_hidden_until_the_vote_closes(
        self, played: Played, clock: FixedClock
    ) -> None:
        winner, runner_up = played.sunday.line[:2]
        self.cast_all(played, winner, runner_up)
        view = selectors.voting_view(played.game_day, played.sunday.line[10])
        assert view.window.is_open
        assert view.results == ()
        clock.set(MONDAY_LIST_OPENS)
        closed = selectors.voting_view(played.game_day, played.sunday.line[10])
        assert closed.winners == (winner,)
        assert closed.results[0].standing.points == 30
        assert closed.results[1].player == runner_up

    def test_a_sunday_nobody_voted_on_has_no_gold_ball(
        self, played: Played, clock: FixedClock
    ) -> None:
        clock.set(MONDAY_LIST_OPENS)
        assert selectors.gold_ball_winners([played.game_day]) == {}

    def test_viewer_cannot_vote_for_themselves(self, played: Played) -> None:
        viewer = played.sunday.line[0]
        view = selectors.voting_view(played.game_day, viewer)
        assert view.can_vote
        assert viewer not in view.nominees
        assert len(view.nominees) == 21

    def test_latest_voting_day(self, played: Played, clock: FixedClock) -> None:
        assert selectors.latest_voting_day() == played.game_day
        clock.set(datetime(2026, 10, 14, 9, 0, tzinfo=SP))
        assert selectors.latest_voting_day() is None


class TestSeasonTable:
    def test_goals_assists_wins_and_presence(self, played: Played) -> None:
        one, two, three = (played.team(n) for n in (1, 2, 3))
        table = selectors.season_table(seasons.current_season(), Metric.GOALS)
        rows = {row.player.pk: row for row in table.rows}
        assert table.sundays == 1
        scorer = rows[one[0].pk]
        assert scorer.position == 1
        assert (scorer.totals.goals, scorer.totals.wins, scorer.totals.matches) == (2, 1, 1)
        assert rows[one[1].pk].totals.assists == 1
        assert rows[two[0].pk].totals.assists == 1
        assert rows[two[0].pk].totals.losses == 1
        assert rows[three[0].pk].totals.draws == 1
        # Present on the list but in no match of the table: still counts as present.
        assert all(row.totals.sundays == 1 for row in table.rows)
        assert len(table.rows) == 22

    def test_gold_balls_only_after_the_vote_closes(self, played: Played, clock: FixedClock) -> None:
        winner = played.sunday.line[0]
        for voter in played.sunday.line[1:5]:
            services.cast_vote(
                played.game_day.pk,
                voter,
                first=winner.pk,
                second=played.sunday.line[5].pk,
                third=played.sunday.line[6].pk,
            )
        current = seasons.current_season()
        before = selectors.season_table(current, Metric.GOLD_BALLS)
        assert all(row.totals.gold_balls == 0 for row in before.rows)
        clock.set(MONDAY_LIST_OPENS)
        after = selectors.season_table(current, Metric.GOLD_BALLS)
        assert after.rows[0].player == winner
        assert after.rows[0].totals.gold_balls == 1
        assert after.rows[0].position == 1
        assert after.rows[1].position == 2

    def test_a_goalkeeper_in_goal_counts_on_their_side(self, played: Played) -> None:
        keeper = played.sunday.goalkeepers[0]
        match = matchday.add_match(
            played.game_day.pk, played.board, home_team=1, away_team=3, home_goalkeeper=keeper.pk
        )
        one = played.team(1)
        matchday.add_goal(match.pk, played.board, side=Side.HOME, scorer=one[0].pk)
        matchday.add_goal(match.pk, played.board, side=Side.HOME, scorer=one[1].pk)
        table = selectors.season_table(seasons.current_season(), Metric.GOALS)
        totals = next(row.totals for row in table.rows if row.player == keeper)
        assert (totals.matches, totals.wins) == (1, 1)

    def test_absent_players_do_not_count(self, played: Played) -> None:
        absent = mark_absent(played, played.team(2)[1])
        table = selectors.season_table(seasons.current_season(), Metric.SUNDAYS)
        assert absent not in [row.player for row in table.rows]

    def test_a_scorer_counts_even_if_marked_absent_afterwards(self, played: Played) -> None:
        scorer = mark_absent(played, played.team(1)[0])
        table = selectors.season_table(seasons.current_season(), Metric.GOALS)
        row = next(row for row in table.rows if row.player == scorer)
        assert (row.totals.goals, row.totals.sundays) == (2, 1)


class TestPlayerProfile:
    def vote_for(self, played: Played, winner: Player, other: Player, third: Player) -> None:
        for voter in played.sunday.line[5:8]:
            services.cast_vote(
                played.game_day.pk, voter, first=winner.pk, second=other.pk, third=third.pk
            )

    def test_sunday_by_sunday_with_the_score_of_each_match(self, played: Played) -> None:
        scorer = played.team(1)[0]
        profile = profiles.load(scorer)
        assert profile.overall.goals == 2
        (sunday,) = profile.sundays
        assert sunday.line.wins == 1
        assert sunday.team == "Time A"
        assert not sunday.won_gold_ball
        (match,) = sunday.matches
        assert (match.team, match.opponent) == ("Time A", "Time B")
        assert (match.mine, match.theirs, match.result, match.goals) == (2, 0, "V", 2)

    def test_matches_agree_with_the_wins_draws_and_losses(self, played: Played) -> None:
        for player in (*played.team(1), *played.team(2), *played.team(3), *played.team(4)):
            (sunday,) = profiles.load(player).sundays
            results = [m.result for m in sunday.matches]
            assert (results.count("V"), results.count("E"), results.count("D")) == (
                sunday.line.wins,
                sunday.line.draws,
                sunday.line.losses,
            )

    def test_a_player_who_never_played(self, make_player: Callable[..., Player]) -> None:
        profile = profiles.load(make_player("Novato"))
        assert profile.sundays == ()
        assert profile.overall.sundays == 0
        assert profile.first_played is None
        assert profile.summary(None).presence is None

    def test_gold_ball_and_podium_only_after_the_vote_closes(
        self, played: Played, clock: FixedClock
    ) -> None:
        winner, other, third = played.sunday.line[:3]
        self.vote_for(played, winner, other, third)
        assert profiles.load(winner).gold_balls == ()
        assert profiles.load(winner).overall.gold_balls == 0
        clock.set(MONDAY_LIST_OPENS)
        assert len(profiles.load(winner).gold_balls) == 1
        assert profiles.load(winner).overall.gold_balls == 1
        assert [len(v) for v in profiles.load(other).runner_ups.values()] == [1, 0]
        assert [len(v) for v in profiles.load(third).runner_ups.values()] == [0, 1]

    def test_summary_by_season(self, played: Played, clock: FixedClock) -> None:
        scorer = played.team(1)[0]
        clock.set(MONDAY_MORNING)
        services.end_season(played.board)
        profile = profiles.load(scorer)
        first, second = Season.objects.order_by("number")
        assert profile.summary(None).totals.goals == 2
        assert profile.summary(first).totals.goals == 2
        assert profile.summary(second).totals == stats.Totals()
        assert profile.summary(second).recent == ()
        assert [row.season for row in profile.sundays] == [1]

    def test_presence_counts_the_sundays_since_the_first_one(self, played: Played) -> None:
        summary = profiles.load(played.team(1)[0]).summary(None)
        assert (summary.totals.sundays, summary.possible, summary.presence) == (1, 1, 100)
        assert summary.goals_per_sunday == 2.0

    def test_origin(self, played: Played, make_player: Callable[..., Player]) -> None:
        scorer = played.team(1)[0]
        friend = make_player("Amigo", referred_by=scorer, member_since=date(2025, 3, 1))
        origin = profiles.load(scorer).origin()
        assert origin.referred_by == played.sunday.line[0].referred_by
        assert origin.first_played == played.game_day.date
        assert [(r.player, r.sundays) for r in origin.referrals] == [(friend, 0)]
        assert profiles.load(friend).origin().joined_on == date(2025, 3, 1)

    def test_joined_on_falls_back_to_the_account_date(self, made: Player) -> None:
        assert made.joined_on == made.created_at.astimezone(SP).date()

    @pytest.fixture
    def made(self, make_player: Callable[..., Player]) -> Player:
        return make_player("Nova")


class TestDirectory:
    def test_filters_by_type_position_and_name(self, make_player: Callable[..., Player]) -> None:
        monthly = make_player("Ana Mensal", player_type="mensalista")
        make_player("Bia Avulsa")
        keeper = make_player("Caio Goleiro", position="goleiro")
        make_player("Dani Inativa", is_active=False)
        names = lambda **kw: [r.player.name for r in profiles.directory(**kw)]  # noqa: E731
        assert names() == ["Ana Mensal", "Bia Avulsa", "Caio Goleiro"]
        assert names(kind="mensalista") == [monthly.name]
        assert names(kind="goleiro") == [keeper.name]
        assert names(query="bia") == ["Bia Avulsa"]

    def test_totals_come_from_the_ranking(self, played: Played) -> None:
        scorer = played.team(1)[0]
        row = next(r for r in profiles.directory() if r.player == scorer)
        assert (row.sundays, row.gold_balls) == (1, 0)


class TestSeasons:
    def test_one_open_season_from_the_start(self) -> None:
        first = seasons.current_season()
        assert (first.number, first.is_open) == (1, True)
        assert seasons.current_season() == first

    def test_ending_starts_the_next_and_splits_the_days(
        self, played: Played, clock: FixedClock
    ) -> None:
        clock.set(MONDAY_MORNING)
        next_season = services.end_season(played.board)
        assert next_season.number == 2
        assert next_season.is_open
        old = Season.objects.get(number=1)
        assert old.ends_on == MONDAY_MORNING.date()
        assert old.ended_by == played.board
        assert list(seasons.game_days_of(old)) == [played.game_day]
        assert list(seasons.game_days_of(next_season)) == []
        assert seasons.season_of(played.game_day.date) == old

        old_table = selectors.season_table(old, Metric.GOALS)
        assert old_table.sundays == 1
        new_table = selectors.season_table(next_season, Metric.GOALS)
        assert new_table.rows == ()
        overall = selectors.season_table(None, Metric.GOALS)
        assert overall.sundays == 1

    def test_the_sunday_it_ends_on_stays_in_the_old_season(
        self, played: Played, clock: FixedClock
    ) -> None:
        services.end_season(played.board)
        assert seasons.season_of(played.game_day.date).number == 1
        later = GameDay.objects.create(date=datetime(2026, 10, 25).date())
        assert seasons.season_of(later.date).number == 2

    def test_only_once_a_day(self, played: Played) -> None:
        services.end_season(played.board)
        with pytest.raises(CommunityError, match="hoje"):
            services.end_season(played.board)

    def test_only_the_board(self, played: Played) -> None:
        with pytest.raises(PermissionDenied):
            services.end_season(played.sunday.line[0])

    def test_a_vote_still_open_counts_for_the_season_of_its_sunday(
        self, played: Played, clock: FixedClock
    ) -> None:
        winner = played.sunday.line[0]
        for voter in played.sunday.line[1:4]:
            services.cast_vote(
                played.game_day.pk,
                voter,
                first=winner.pk,
                second=played.sunday.line[5].pk,
                third=played.sunday.line[6].pk,
            )
        services.end_season(played.board)
        assert selectors.open_votes_in(Season.objects.get(number=1)) == [played.game_day]
        clock.set(MONDAY_LIST_OPENS)
        old = selectors.season_table(Season.objects.get(number=1), Metric.GOLD_BALLS)
        new = selectors.season_table(Season.objects.get(number=2), Metric.GOLD_BALLS)
        assert old.rows[0].totals.gold_balls == 1
        assert new.rows == ()
