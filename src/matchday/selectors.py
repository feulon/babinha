"""Read models for the game day screens."""

from dataclasses import dataclass
from datetime import date, time

from django.db.models import Prefetch

from accounts.models import Player
from core import clock, demo
from games.domain.schedule import TEAMS_OUT_AT
from games.models import GameDay
from games.services import schedule_for
from matchday.domain.score import Score
from matchday.grouping import first_timers
from matchday.models import Goal, Lineup, LineupSpot, Match, Team
from matchday.services import NextUp, line_players, lineup_for, list_goalkeepers, next_up


@dataclass(frozen=True)
class TeamRoster:
    team: Team
    players: tuple[Player, ...]

    @property
    def pote_sum(self) -> int:
        """Lower is stronger (pote 1 is the best). Only the diretoria ever sees it."""
        return sum(player.pote for player in self.players)


@dataclass(frozen=True)
class Teams:
    """A Sunday's lineup as it stands, next to who is on the list right now."""

    game_day: GameDay
    lineup: Lineup | None
    teams: tuple[TeamRoster, ...]
    in_goal: tuple[Player, ...]  # line players drawn to keep goal
    goalkeepers: tuple[Player, ...]  # the list's goalkeepers
    unassigned: tuple[Player, ...]  # on the list, but joined after the draw
    gone: tuple[Player, ...]  # drawn, but no longer on the list (or marked absent)
    repoted: tuple[
        tuple[Player, int], ...
    ] = ()  # on the teams, but changed pote since (player, old)

    @property
    def in_sync(self) -> bool:
        return not self.unassigned and not self.gone and not self.repoted

    @property
    def line_count(self) -> int:
        return sum(len(t.players) for t in self.teams) + len(self.in_goal) + len(self.unassigned)


def teams_of(game_day: GameDay) -> Teams:
    lineup = lineup_for(game_day)
    playing = line_players(game_day)
    goalkeepers = tuple(list_goalkeepers(game_day))
    if lineup is None:
        return Teams(game_day, None, (), (), goalkeepers, tuple(playing), ())

    playing_ids = {player.pk for player in playing}
    spots = list(
        LineupSpot.objects.filter(lineup=lineup)
        .select_related("player")
        .order_by("player__pote", "player__name")
    )
    drawn_ids = {spot.player_id for spot in spots}
    current = [spot for spot in spots if spot.player_id in playing_ids]
    teams = tuple(
        TeamRoster(team, tuple(s.player for s in current if s.team_id == team.pk))
        for team in lineup.teams.all()
    )
    return Teams(
        game_day=game_day,
        lineup=lineup,
        teams=teams,
        in_goal=tuple(s.player for s in current if s.team_id is None),
        goalkeepers=goalkeepers,
        unassigned=tuple(p for p in playing if p.pk not in drawn_ids),
        gone=tuple(s.player for s in spots if s.player_id not in playing_ids),
        repoted=tuple(
            (s.player, s.pote) for s in current if s.pote is not None and s.pote != s.player.pote
        ),
    )


def lineup_state(state: Teams) -> str:
    """Where the teams stand: none, stale (out of sync with the list), draft or confirmed."""
    if state.lineup is None:
        return "none"
    if not state.in_sync:
        return "stale"
    return "confirmed" if state.lineup.is_confirmed else "draft"


@dataclass(frozen=True)
class MatchRow:
    number: int
    match: Match
    score: Score
    goals: tuple[Goal, ...]


def match_rows(lineup: Lineup) -> tuple[MatchRow, ...]:
    goals = Goal.objects.select_related("scorer", "assist", "team")
    matches = (
        Match.objects.filter(lineup=lineup)
        .select_related("home_team", "away_team", "home_goalkeeper", "away_goalkeeper")
        .prefetch_related(Prefetch("goals", queryset=goals))
    )
    return tuple(_row(number, match) for number, match in enumerate(matches, start=1))


def match_row(match: Match) -> MatchRow:
    number = Match.objects.filter(lineup_id=match.lineup_id, order__lt=match.order).count() + 1
    return _row(number, match)


def _row(number: int, match: Match) -> MatchRow:
    goals = tuple(match.goals.all())
    home = sum(1 for goal in goals if goal.team_id == match.home_team_id)
    return MatchRow(number, match, Score(home, len(goals) - home), goals)


@dataclass(frozen=True)
class PublicTeam:
    name: str
    number: int
    players: tuple[Player, ...]
    is_mine: bool
    adjusting: bool = False  # someone left this team after it was confirmed: it is incomplete


@dataclass(frozen=True)
class DayView:
    """What every player sees of a Sunday. Teams only once confirmed; never the potes."""

    game_day: GameDay
    published: bool
    teams: tuple[PublicTeam, ...]
    goalkeepers: tuple[Player, ...]
    in_goal: tuple[Player, ...]
    matches: tuple[MatchRow, ...]
    my_team: str | None
    in_goal_me: bool  # the viewer keeps goal (list goalkeeper or line player drawn to goal)
    over: bool = False  # the game window of this Sunday has ended
    adjusting: bool = False  # the list changed after the teams were confirmed: being adjusted
    current: MatchRow | None = None  # first match not yet ended: the one on court, or next up
    mine: MatchRow | None = (
        None  # first unfinished match of the viewer's team (or where they keep goal)
    )
    rookies: frozenset[int] = frozenset()  # ids of the first-timers among the teams' players
    upcoming: NextUp | None = None  # the waiting line's next match, once the last one ended
    upcoming_mine: bool = False  # the viewer's team (or goal) plays that match

    @property
    def teams_mine_first(self) -> tuple[PublicTeam, ...]:
        """The viewer's team on top, the others in their usual order."""
        return tuple(sorted(self.teams, key=lambda team: not team.is_mine))

    @property
    def teams_out_label(self) -> str:
        """When the teams usually come out, for the wait text: `9h` or `9h30`."""
        return hour_label(TEAMS_OUT_AT)

    @property
    def mine_is_current(self) -> bool:
        return (
            self.mine is not None
            and self.current is not None
            and self.mine.number == self.current.number
        )

    @property
    def current_started(self) -> bool:
        return self.current is not None and bool(self.current.goals)

    @property
    def featured(self) -> MatchRow | None:
        """The match the strip opens on: the one on court or next up, else the last one played."""
        return self.current or (self.matches[-1] if self.matches else None)

    @property
    def finished_matches(self) -> int:
        return sum(1 for row in self.matches if row.match.is_finished)

    @property
    def total_goals(self) -> int:
        return sum(len(row.goals) for row in self.matches)


def hour_label(at: time) -> str:
    return f"{at.hour}h{at.minute:02d}" if at.minute else f"{at.hour}h"


def _involves(row: MatchRow, team_id: int | None, viewer: Player) -> bool:
    match = row.match
    return team_id in (match.home_team_id, match.away_team_id) or viewer.pk in (
        match.home_goalkeeper_id,
        match.away_goalkeeper_id,
    )


def _plays(upcoming: NextUp, team_id: int | None, viewer: Player) -> bool:
    return team_id in (upcoming.home.pk, upcoming.away.pk) or viewer.pk in (
        upcoming.home_goalkeeper_id,
        upcoming.away_goalkeeper_id,
    )


def day_view(game_day: GameDay, viewer: Player) -> DayView:
    lineup = lineup_for(game_day)
    over = game_day.is_finished or (
        not demo.is_on() and clock.now() >= schedule_for(game_day.date).ends
    )
    if lineup is None or not lineup.is_confirmed or game_day.is_cancelled:
        return DayView(game_day, False, (), (), (), (), None, False, over)

    playing_ids = {player.pk for player in line_players(game_day)}
    all_spots = list(
        LineupSpot.objects.filter(lineup=lineup).select_related("player").order_by("player__name")
    )
    spots = [spot for spot in all_spots if spot.player_id in playing_ids]
    gone = [spot for spot in all_spots if spot.player_id not in playing_ids]
    short_teams = {spot.team_id for spot in gone}
    teams = tuple(
        PublicTeam(
            team.name,
            team.number,
            players := tuple(s.player for s in spots if s.team_id == team.pk),
            any(p.pk == viewer.pk for p in players),
            team.pk in short_teams,
        )
        for team in lineup.teams.all()
    )
    goalkeepers = tuple(list_goalkeepers(game_day))
    in_goal = tuple(s.player for s in spots if s.team_id is None)
    rows = match_rows(lineup)
    unfinished = [row for row in rows if not row.match.is_finished]
    my_team_id = next((s.team_id for s in spots if s.player_id == viewer.pk and s.team_id), None)
    upcoming = None if over else next_up(lineup)
    return DayView(
        game_day=game_day,
        published=True,
        teams=teams,
        goalkeepers=goalkeepers,
        in_goal=in_goal,
        matches=rows,
        my_team=next((t.name for t in teams if t.is_mine), None),
        in_goal_me=any(p.pk == viewer.pk for p in (*goalkeepers, *in_goal)),
        rookies=frozenset(first_timers(game_day, (p.pk for t in teams for p in t.players))),
        over=over,
        adjusting=bool(gone) or len(spots) < len(playing_ids),
        current=unfinished[0] if unfinished else None,
        mine=next((row for row in unfinished if _involves(row, my_team_id, viewer)), None),
        upcoming=upcoming,
        upcoming_mine=upcoming is not None and _plays(upcoming, my_team_id, viewer),
    )


def past_days(*, before: date, limit: int) -> tuple[GameDay, ...]:
    """Earlier Sundays whose teams were published, most recent first."""
    return tuple(
        GameDay.objects.filter(
            date__lt=before, lineup__confirmed_at__isnull=False, cancelled_at=None
        ).order_by("-date")[:limit]
    )
