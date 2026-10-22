"""What a player's profile says, from plain facts about Sundays (no database)."""

from collections.abc import Sequence
from dataclasses import dataclass

from community.domain import voting
from community.domain.stats import DayFact, Side

WINNER = 1
RUNNER_UP_PLACES = (2, 3)


@dataclass(frozen=True)
class MatchRow:
    """One finished match, from the point of view of the player."""

    order: int  # 1 is the first match of the Sunday
    team: str
    opponent: str
    mine: int
    theirs: int
    result: str  # "V", "E" or "D"
    goals: int
    assists: int


def presence_percent(attended: int, possible: int) -> int | None:
    """Share of the Sundays played since the player's first one that they went to."""
    if possible <= 0:
        return None
    return min(100, round(100 * attended / possible))


def podium_places(ranked: Sequence[voting.Standing]) -> dict[int, int]:
    """The place of each player on a closed vote: everyone tied for first is 1, then the
    others by position (a tie shares the place of its first member)."""
    winner_ids = {row.player_id for row in voting.winners(ranked)}
    places: dict[int, int] = {}
    previous_key = None
    position = 0
    for index, row in enumerate(ranked):
        if row.player_id in winner_ids:
            places[row.player_id] = WINNER
            continue
        if row.sort_key != previous_key:
            position = index + 1
            previous_key = row.sort_key
        places[row.player_id] = position
    return places


def match_rows(day: DayFact, player_id: int) -> tuple[MatchRow, ...]:
    """The finished matches the player took part in, in the order they were played."""
    rows = []
    for order, match in enumerate(day.matches, start=1):
        if not match.finished:
            continue
        side = match.sides().get(player_id)
        if side is None:
            continue
        home, away = match.score()
        home_side = side == Side.HOME
        mine, theirs = (home, away) if home_side else (away, home)
        rows.append(
            MatchRow(
                order=order,
                team=match.home_name if home_side else match.away_name,
                opponent=match.away_name if home_side else match.home_name,
                mine=mine,
                theirs=theirs,
                result="V" if mine > theirs else "D" if mine < theirs else "E",
                goals=sum(1 for goal in match.goals if goal.scorer_id == player_id),
                assists=sum(1 for goal in match.goals if goal.assist_id == player_id),
            )
        )
    return tuple(rows)
