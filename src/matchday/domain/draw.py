"""Team draw: four teams of line players balanced by pote.

Pure functions; `matchday.services` loads the players and persists the result.

Rules (docs/plan.md "Dia do jogo"):
- Four teams of five line players. Goalkeepers have no pote and are not drawn into teams.
- Line players beyond the 20 team spots (1 or 2) complete the goal, and the draw picks who.
- Teams are balanced by pote (1 best, 5 worst): the sum of the potes of each team is as close as
  possible to the others', and among the draws that are equally close every team gets the same
  number of players of each pote when possible. Everything else is random, so the teams change
  every week.
- Players tied together (see `matchday.domain.groups`) are one unit: they are never split, and
  only go to the goal whole, when there are not enough players alone to complete it.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from matchday.domain.balance import Imbalance, TeamLoad, assess
from matchday.domain.balancing import DrawPlayer, Team, Unit, floor, improve, score
from matchday.domain.packing import fits, oversized_capacities, place_groups
from matchday.domain.sizes import TEAM_COUNT, TEAM_SIZE, team_sizes

__all__ = [
    "TEAM_COUNT",
    "TEAM_SIZE",
    "Conflict",
    "Draw",
    "DrawConflict",
    "DrawError",
    "DrawPlayer",
    "draw_teams",
    "team_sizes",
]

# The deal is repeated from random starts, each one improved by swapping players (or groups)
# between teams (`matchday.domain.balancing`); the most balanced result wins. The search stops
# early once this many starts in a row bring nothing better, and never runs more than
# SEARCH_STARTS.
SEARCH_STARTS = 30
SEARCH_PATIENCE = 8


class DrawError(Exception):
    """A draw that cannot be made. `message` is shown to the diretoria (pt-BR)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Conflict:
    """Why groups cannot be dealt.

    - `too_big`: one group has more players than a team can hold (`capacity`).
    - `no_fit`: every group fits a team, but not all of them together.
    - `goal`: the goal needs players and only whole groups are left to take them from.
    """

    kind: Literal["too_big", "no_fit", "goal"]
    groups: tuple[tuple[int, ...], ...]
    capacity: int


class DrawConflict(DrawError):
    """The groups cannot be dealt into the teams."""

    def __init__(self, conflicts: Sequence[Conflict]) -> None:
        super().__init__("Não dá para sortear os times mantendo os grupos juntos.")
        self.conflicts = tuple(conflicts)


@dataclass(frozen=True)
class Draw:
    teams: tuple[tuple[int, ...], ...]  # player ids of each team, team 1 first
    in_goal: tuple[int, ...]  # line players drawn to complete the goal
    warnings: tuple[Imbalance, ...] = ()  # how uneven the teams came out, if they did


def draw_teams(
    players: Sequence[DrawPlayer],
    rng: random.Random,
    *,
    groups: Sequence[Sequence[int]] = (),
    allow_oversized: bool = False,
    team_count: int = TEAM_COUNT,
    team_size: int = TEAM_SIZE,
    balance: bool = True,
) -> Draw:
    """Draw the teams.

    Players alone are dealt pote by pote, best pote first, each one to the team with the fewest
    players of that pote; ties go to the smaller team, then to the weaker one (higher pote sum),
    then at random. That keeps the count of each pote per team within one of the others, and
    hands a pote's leftover players to the teams that need them most.

    `groups` are sets of player ids that must share a team. They are placed first, at random but
    keeping every group whole, and the players alone fill the rest.

    That deal is only the start. Swapping players (or whole groups) between teams, while the sizes
    stay the same, shrinks the gap between the team with the highest pote sum and the one with the
    lowest; the draws with the smallest gap compete on how evenly each pote is spread, and a
    random number breaks the remaining ties. Several random starts are tried and the best result
    wins. `balance=False` skips all that (one deal, as is) for callers that only need to know
    whether the groups can be dealt.

    `allow_oversized` lets one team grow to hold a group bigger than a team; raises
    `DrawConflict` when the groups cannot be dealt.
    """
    ids = [p.player_id for p in players]
    if len(set(ids)) != len(ids):
        raise ValueError("each player can be drawn only once")
    if len(players) < team_count:
        raise DrawError(
            f"São precisos pelo menos {team_count} jogadores de linha para sortear os times."
        )
    multi = _checked_groups(groups, set(ids))

    pool = list(players)
    rng.shuffle(pool)
    grouped = {pid for group in multi for pid in group}
    singles = [p for p in pool if p.player_id not in grouped]
    excess = max(0, len(pool) - team_count * team_size)
    goal_singles, goal_groups = singles[:excess], _goal_groups(multi, excess - len(singles), rng)
    by_id = {p.player_id: p for p in players}
    in_goal = [*goal_singles, *(by_id[pid] for group in goal_groups for pid in group)]
    on_groups = [group for group in multi if group not in goal_groups]
    on_singles = singles[excess:]

    capacities = _capacities(
        len(pool) - len(in_goal), on_groups, rng, allow_oversized, team_count, team_size
    )
    sizes = [len(group) for group in on_groups]

    best: tuple[tuple[int, int, float], list[Team]] | None = None
    stale = 0
    for _ in range(SEARCH_STARTS if balance else 1):
        teams = [Team(capacity) for capacity in capacities]
        for group, index in zip(on_groups, place_groups(sizes, capacities, rng) or [], strict=True):
            teams[index].units.append(Unit.of(by_id[pid] for pid in group))
        _deal(teams, on_singles, rng, shuffle=bool(on_groups))
        if balance:
            improve(teams, rng)
        tried = (*score(teams), rng.random())
        stale = 0 if best is None or tried[:2] < best[0][:2] else stale + 1
        if best is None or tried < best[0]:
            best = (tried, teams)
        if stale >= SEARCH_PATIENCE or (best[0][:2] == (floor(teams), 0)):
            break  # no patience left, or nothing can be more even than this
    assert best is not None  # the loop runs at least once
    teams = best[1]

    return Draw(
        teams=tuple(tuple(p.player_id for p in team.players) for team in teams),
        in_goal=tuple(p.player_id for p in in_goal),
        warnings=assess(
            [TeamLoad(tuple(p.pote for p in team.players)) for team in teams],
            len(in_goal),
            team_size=team_size,
        ),
    )


def _checked_groups(groups: Sequence[Sequence[int]], known: set[int]) -> list[tuple[int, ...]]:
    multi = [tuple(group) for group in groups if len(group) >= 2]
    flat = [pid for group in multi for pid in group]
    if len(set(flat)) != len(flat):
        raise ValueError("a player can be in only one group")
    if not set(flat) <= known:
        raise ValueError("a group has a player who is not being drawn")
    return multi


def _goal_groups(
    groups: list[tuple[int, ...]], needed: int, rng: random.Random
) -> list[tuple[int, ...]]:
    """Whole groups to complete the goal when the players alone are not enough: `needed` players."""
    if needed <= 0:
        return []
    order = list(groups)
    rng.shuffle(order)

    def search(start: int, left: int) -> list[tuple[int, ...]] | None:
        if left == 0:
            return []
        for i in range(start, len(order)):
            if len(order[i]) <= left and (rest := search(i + 1, left - len(order[i]))) is not None:
                return [order[i], *rest]
        return None

    found = search(0, needed)
    if found is None:
        raise DrawConflict([Conflict("goal", tuple(groups), needed)])
    return found


def _capacities(
    on_teams: int,
    groups: list[tuple[int, ...]],
    rng: random.Random,
    allow_oversized: bool,
    team_count: int,
    team_size: int,
) -> list[int]:
    sizes = [len(group) for group in groups]
    capacities = list(team_sizes(on_teams, team_count=team_count, team_size=team_size))
    if sizes and not fits(sizes, capacities) and allow_oversized:
        capacities = list(oversized_capacities(capacities, sizes))
    if sizes and not fits(sizes, capacities):
        biggest = max(capacities)
        too_big = tuple(group for group in groups if len(group) > biggest)
        if too_big:
            raise DrawConflict([Conflict("too_big", (group,), biggest) for group in too_big])
        raise DrawConflict([Conflict("no_fit", tuple(groups), biggest)])
    rng.shuffle(capacities)  # when short, which teams play with one less is random too
    return capacities


def _deal(
    teams: list[Team], singles: Sequence[DrawPlayer], rng: random.Random, *, shuffle: bool
) -> None:
    order = list(singles)
    if shuffle:
        rng.shuffle(order)  # the first shuffle already randomized the order within each pote
    for player in sorted(order, key=lambda p: p.pote):
        candidates = [
            ((team.count(player.pote), len(team.players), -team.pote_sum, rng.random()), team)
            for team in teams
            if team.has_room
        ]
        _, chosen = min(candidates, key=lambda candidate: candidate[0])
        chosen.units.append(Unit.of((player,)))
