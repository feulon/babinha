"""Season statistics, from plain facts about matches (no database).

A player's side in a match is, in order: goalkeeper of that side, member of its team, or scorer
or assist for it (a substitute). Only finished matches count for matches, wins and presence;
every goal counts for goals and assists.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Side(StrEnum):
    HOME = "home"
    AWAY = "away"


@dataclass(frozen=True)
class GoalFact:
    side: Side
    scorer_id: int | None = None
    assist_id: int | None = None


@dataclass(frozen=True)
class MatchFact:
    finished: bool
    home_players: frozenset[int]
    away_players: frozenset[int]
    goals: tuple[GoalFact, ...] = ()
    home_name: str = ""  # team names, only for screens that show the match
    away_name: str = ""

    def score(self) -> tuple[int, int]:
        home = sum(1 for goal in self.goals if goal.side == Side.HOME)
        return home, len(self.goals) - home

    def sides(self) -> dict[int, Side]:
        """Each player's side. Substitutes are found through their goals and assists."""
        found: dict[int, Side] = {}
        for goal in self.goals:
            for player_id in (goal.scorer_id, goal.assist_id):
                if player_id is not None:
                    found[player_id] = goal.side
        found.update(dict.fromkeys(self.away_players, Side.AWAY))
        found.update(dict.fromkeys(self.home_players, Side.HOME))
        return found


@dataclass(frozen=True)
class DayFact:
    day_id: int
    date: date
    attendees: frozenset[int]  # on the list and not marked absent, plus anyone who played
    matches: tuple[MatchFact, ...] = ()

    @property
    def played(self) -> bool:
        return any(match.finished or match.goals for match in self.matches)


@dataclass
class Line:
    """What one player did on one Sunday."""

    present: bool = False
    matches: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals: int = 0
    assists: int = 0


@dataclass
class Totals:
    sundays: int = 0
    matches: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals: int = 0
    assists: int = 0
    gold_balls: int = 0


def day_lines(day: DayFact) -> dict[int, Line]:
    """Per-player lines for one Sunday. Empty when nothing was played."""
    if not day.played:
        return {}
    lines = {player_id: Line(present=True) for player_id in day.attendees}
    for match in day.matches:
        for goal in match.goals:
            if goal.scorer_id is not None:
                lines.setdefault(goal.scorer_id, Line(present=True)).goals += 1
            if goal.assist_id is not None:
                lines.setdefault(goal.assist_id, Line(present=True)).assists += 1
        if not match.finished:
            continue
        home, away = match.score()
        for player_id, side in match.sides().items():
            line = lines.setdefault(player_id, Line(present=True))
            line.matches += 1
            mine, theirs = (home, away) if side == Side.HOME else (away, home)
            if mine > theirs:
                line.wins += 1
            elif mine < theirs:
                line.losses += 1
            else:
                line.draws += 1
    return lines


def totals(
    days: Sequence[DayFact], gold_ball_winners: Mapping[int, Iterable[int]]
) -> dict[int, Totals]:
    """Accumulated numbers per player. `gold_ball_winners` maps a day id to its winners."""
    result: dict[int, Totals] = {}
    for day in days:
        for player_id, line in day_lines(day).items():
            total = result.setdefault(player_id, Totals())
            total.sundays += int(line.present)
            total.matches += line.matches
            total.wins += line.wins
            total.draws += line.draws
            total.losses += line.losses
            total.goals += line.goals
            total.assists += line.assists
    for winner_ids in gold_ball_winners.values():
        for player_id in winner_ids:
            result.setdefault(player_id, Totals()).gold_balls += 1
    return result
