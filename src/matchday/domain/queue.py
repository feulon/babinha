"""The Sunday's waiting line: who plays the next match.

Every match ends at two goals or when the time runs out. The winner stays on court and the
loser goes to the back of the line; after a draw both leave. Whoever enters is whoever has
waited the longest, counted from the last match they played.
"""

from collections.abc import Sequence
from dataclasses import dataclass

MIN_TEAMS = 2


@dataclass(frozen=True)
class PlayedMatch:
    """A match that ended, with the teams (by id) and goalkeepers (player ids) of each side."""

    home: int
    away: int
    home_goals: int
    away_goals: int
    home_goalkeeper: int | None = None
    away_goalkeeper: int | None = None

    @property
    def teams(self) -> tuple[int, int]:
        return self.home, self.away

    @property
    def winner(self) -> int | None:
        if self.home_goals > self.away_goals:
            return self.home
        if self.away_goals > self.home_goals:
            return self.away
        return None

    def goalkeeper_of(self, team: int) -> int | None:
        return self.home_goalkeeper if team == self.home else self.away_goalkeeper


def _staying(played: Sequence[PlayedMatch]) -> int | None:
    """The team that stays on court after the last match: its winner, if there was one."""
    return played[-1].winner if played else None


def waiting_line(teams: Sequence[int], played: Sequence[PlayedMatch]) -> list[int]:
    """The teams that are off court, the one who has waited the longest first.

    Teams that have not played yet come first, in the order of `teams`. A team that left the
    last match is at the back; among teams that left the same match (a draw), the one with
    fewer matches played goes first, then the order of `teams`.
    """
    order = {team: n for n, team in enumerate(teams)}
    last_played = dict.fromkeys(teams, -1)
    matches_played = dict.fromkeys(teams, 0)
    for index, match in enumerate(played):
        for team in match.teams:
            last_played[team] = index
            matches_played[team] += 1
    staying = _staying(played)
    off_court = [team for team in teams if team != staying]
    return sorted(off_court, key=lambda t: (last_played[t], matches_played[t], order[t]))


def next_matchup(teams: Sequence[int], played: Sequence[PlayedMatch]) -> tuple[int, int]:
    """The two teams of the next match (home, away), given the matches that ended in order.

    The first match is between the first two of `teams`. Afterwards the winner stays and meets
    the team that has waited the longest; after a draw both leave and the two who waited the
    longest come in. With three teams a draw brings in the one team that was off court and,
    of the two that just left, the one that has played less.
    """
    if len(teams) < MIN_TEAMS:
        raise ValueError("a match needs at least two teams")
    staying = _staying(played)
    stays = [] if staying is None else [staying]
    first, second = (*stays, *waiting_line(teams, played)[: MIN_TEAMS - len(stays)])
    return first, second


def next_goalkeepers(
    pool: Sequence[int], played: Sequence[PlayedMatch], matchup: tuple[int, int]
) -> tuple[int | None, int | None]:
    """Home and away goalkeeper of the next match, from the `pool` of player ids.

    A team that stays on court keeps its goalkeeper; the empty seats go to whoever has kept
    goal the longest ago (never, first), in the order of the pool. A seat with nobody left in
    the pool stays open (None), for the diretoria to fill in.
    """
    last_kept = dict.fromkeys(pool, -1)
    for index, match in enumerate(played):
        for keeper in (match.home_goalkeeper, match.away_goalkeeper):
            if keeper in last_kept:
                last_kept[keeper] = index
    seats: list[int | None] = [None, None]
    stayer = _staying(played)
    if stayer in matchup:
        keeper = played[-1].goalkeeper_of(stayer)
        seats[matchup.index(stayer)] = keeper if keeper in last_kept else None
    free = sorted(
        (keeper for keeper in pool if keeper not in seats),
        key=lambda keeper: (last_kept[keeper], pool.index(keeper)),
    )
    for seat in range(len(seats)):
        if seats[seat] is None and free:
            seats[seat] = free.pop(0)
    return seats[0], seats[1]
