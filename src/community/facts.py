"""Facts about past Sundays, read from the game day models as plain data.

This is the only place the community code reads who played in which match. Rankings and the
Bola de Ouro both go through `load_days`.
"""

from collections import defaultdict
from collections.abc import Iterable

from community.domain.stats import DayFact, GoalFact, MatchFact, Side
from games.domain.types import EntryStatus
from games.models import Attendance, GameDay, ListEntry
from matchday.models import LineupSpot, Match


def load_days(game_days: Iterable[GameDay]) -> list[DayFact]:
    """Facts for the given Sundays, in the order given. Cancelled Sundays have no facts."""
    days = [day for day in game_days if not day.is_cancelled]
    ids = [day.pk for day in days]

    on_list: dict[int, set[int]] = defaultdict(set)
    entries = ListEntry.objects.filter(game_day_id__in=ids, status=EntryStatus.LISTA.value)
    for day_id, player_id in entries.exclude(attendance=Attendance.FALTOU).values_list(
        "game_day_id", "player_id"
    ):
        on_list[day_id].add(player_id)

    teams: dict[int, set[int]] = defaultdict(set)  # team id -> players
    spots = LineupSpot.objects.filter(lineup__game_day_id__in=ids, team__isnull=False)
    for team_id, player_id in spots.values_list("team_id", "player_id"):
        teams[team_id].add(player_id)

    matches: dict[int, list[MatchFact]] = defaultdict(list)
    rows = (
        Match.objects.filter(lineup__game_day_id__in=ids)
        .select_related("lineup", "home_team", "away_team")
        .prefetch_related("goals")
        .order_by("lineup__game_day_id", "order")
    )
    for match in rows:
        fact = _match_fact(match, teams, on_list[match.lineup.game_day_id])
        matches[match.lineup.game_day_id].append(fact)

    return [
        DayFact(
            day.pk,
            day.date,
            frozenset(on_list[day.pk] | _played(matches[day.pk])),
            tuple(matches[day.pk]),
        )
        for day in days
    ]


def _played(matches: Iterable[MatchFact]) -> set[int]:
    return {player_id for match in matches for player_id in match.sides()}


def _match_fact(match: Match, teams: dict[int, set[int]], on_list: set[int]) -> MatchFact:
    """Whoever left the list or was marked absent after the draw did not play, unless a goal
    says otherwise."""
    home = teams[match.home_team_id] & on_list
    away = teams[match.away_team_id] & on_list
    # The goalkeeper of a side plays for it, even when drawn to a team of the other side.
    for keeper_id, mine, theirs in (
        (match.home_goalkeeper_id, home, away),
        (match.away_goalkeeper_id, away, home),
    ):
        if keeper_id in on_list:
            theirs.discard(keeper_id)
            mine.add(keeper_id)
    goals = tuple(
        GoalFact(
            Side.HOME if goal.team_id == match.home_team_id else Side.AWAY,
            goal.scorer_id,
            goal.assist_id,
        )
        for goal in match.goals.all()
    )
    return MatchFact(
        match.is_finished,
        frozenset(home),
        frozenset(away),
        goals,
        match.home_team.name,
        match.away_team.name,
    )
