"""Evening out the pote sums of drawn teams by swapping players (and whole groups) between them.

The draw first deals the teams; this is the search that follows. A swap trades units of the same
total size between two teams, so the team sizes never change, and a unit is a player alone or a
group, so a group is never opened. The goal is the smallest gap between the team with the highest
pote sum and the one with the lowest (`spread`); among draws with the same gap, the one that
spreads each pote most evenly across the teams (`cost`) is better.

Pure functions over `Team`s, which they change in place.
"""

import random
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import combinations

# A swap trades up to this many units (a player alone, or a whole group) on each side.
SWAP_UNITS = 2
# A result nothing improves is shaken with this many random swaps, then improved again, this many
# times, keeping whatever turns out better: a way out of dead ends that no single swap escapes.
KICKS = 4
KICK_SWAPS = 2

Score = tuple[int, int]  # (spread, cost): lower is better


@dataclass(frozen=True)
class DrawPlayer:
    player_id: int
    pote: int


@dataclass(frozen=True, eq=False)
class Unit:
    """A player alone, or a group that is never split, with its size and pote sum worked out."""

    members: tuple[DrawPlayer, ...]
    size: int
    total: int

    @classmethod
    def of(cls, members: Iterable[DrawPlayer]) -> "Unit":
        picks = tuple(members)
        return cls(picks, len(picks), sum(p.pote for p in picks))


@dataclass
class Team:
    capacity: int
    units: list[Unit] = field(default_factory=list)

    @property
    def players(self) -> list[DrawPlayer]:
        return [p for unit in self.units for p in unit.members]

    @property
    def size(self) -> int:
        return sum(unit.size for unit in self.units)

    @property
    def has_room(self) -> bool:
        return self.size < self.capacity

    @property
    def pote_sum(self) -> int:
        return sum(unit.total for unit in self.units)

    def count(self, pote: int) -> int:
        return sum(1 for p in self.players if p.pote == pote)


@dataclass(frozen=True)
class _Trade:
    """Units one team hands over in a swap, with what a swap needs to know about them."""

    units: tuple[Unit, ...]
    size: int
    total: int
    potes: tuple[int, ...]


def spread(teams: Sequence[Team]) -> int:
    """The gap between the highest and the lowest pote sum of a team: what the draw minimizes."""
    sums = [team.pote_sum for team in teams]
    return max(sums) - min(sums)


def cost(teams: Sequence[Team]) -> int:
    """How far the potes are from an even split: lower is more even.

    The sum of the squares of how far each team's count of a pote is from the average, scaled by
    the number of teams so it stays a whole number (and compares exactly).
    """
    potes = {p.pote for team in teams for p in team.players}
    total = {pote: sum(team.count(pote) for team in teams) for pote in potes}
    return sum(
        (team.count(pote) * len(teams) - total[pote]) ** 2 for team in teams for pote in potes
    )


def score(teams: Sequence[Team]) -> Score:
    return (spread(teams), cost(teams))


def floor(teams: Sequence[Team]) -> int:
    """The smallest spread any draw of these teams can have: 0, or 1 when equal teams cannot tie."""
    equal = len({team.capacity for team in teams}) == 1
    total = sum(p.pote for team in teams for p in team.players)
    return 1 if equal and total % len(teams) else 0


def improve(teams: list[Team], rng: random.Random) -> None:
    """Swap units between teams, one improving swap at a time, until none improves the draw.

    Then, unless the spread already is as small as it can be, shake the result a few times:
    random swaps, improved again, kept only when better.
    """
    current = _climb(teams, rng)
    for _ in range(KICKS):
        if current[0] <= floor(teams):
            return
        kicked = [Team(team.capacity, list(team.units)) for team in teams]
        for _ in range(KICK_SWAPS):
            if swaps := _swaps(kicked, rng):  # shuffled: any one is a random swap
                _swap(kicked, *swaps[0])
        kicked_score = _climb(kicked, rng)
        if kicked_score < current:
            current = kicked_score
            for team, better in zip(teams, kicked, strict=True):
                team.units = better.units


def _climb(teams: list[Team], rng: random.Random) -> Score:
    current = score(teams)
    while (better := _improving_swap(teams, current, rng)) is not None:
        current = better
    return current


def _improving_swap(teams: list[Team], current: Score, rng: random.Random) -> Score | None:
    """Make the first swap (in random order) that lowers the score; the new score, or None."""
    sums = [team.pote_sum for team in teams]
    for first, second, give, take in _swaps(teams, rng):
        after = list(sums)
        after[first] += take.total - give.total
        after[second] += give.total - take.total
        gap = max(after) - min(after)
        if gap > current[0]:
            continue
        before = (teams[first].units, teams[second].units)
        _swap(teams, first, second, give, take)
        better = (gap, cost(teams))
        if better < current:
            return better
        teams[first].units, teams[second].units = before
    return None


def _swap(teams: list[Team], first: int, second: int, give: _Trade, take: _Trade) -> None:
    """Trade `give` (of team `first`) for `take` (of team `second`)."""
    teams[first].units = [*(u for u in teams[first].units if u not in give.units), *take.units]
    teams[second].units = [*(u for u in teams[second].units if u not in take.units), *give.units]


def _swaps(teams: Sequence[Team], rng: random.Random) -> list[tuple[int, int, _Trade, _Trade]]:
    """Every swap of units of the same total size between two teams that changes the potes, in
    random order: the units `give` of team `first` for the units `take` of team `second`."""
    trades = [list(_trades(team)) for team in teams]
    found = [
        (first, second, give, take)
        for first, second in combinations(range(len(teams)), 2)
        for give in trades[first]
        for take in trades[second]
        if give.size == take.size and give.potes != take.potes
    ]
    rng.shuffle(found)
    return found


def _trades(team: Team) -> Iterator[_Trade]:
    for count in range(1, SWAP_UNITS + 1):
        for units in combinations(team.units, count):
            potes = sorted(p.pote for unit in units for p in unit.members)
            yield _Trade(
                units, sum(u.size for u in units), sum(u.total for u in units), tuple(potes)
            )
