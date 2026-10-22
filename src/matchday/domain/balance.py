"""How even the teams are. Only ever a warning: the diretoria can confirm uneven teams.

Pure functions over per-team loads (team index = position in the sequence).
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from matchday.domain.sizes import TEAM_SIZE, even_split, goal_spots

# A draw dealt pote by pote keeps these small, so they only fire when groups or hand edits skew
# the teams. Average pote further apart than this makes one team clearly stronger than another.
POTE_AVERAGE_SPREAD = 1.5
# Teams may differ by a player or two of a pote just from how the potes fall; three is a skew.
POTE_COUNT_SPREAD = 3


@dataclass(frozen=True)
class TeamLoad:
    """Who one team holds: its players' potes."""

    potes: tuple[int, ...]

    @property
    def count(self) -> int:
        return len(self.potes)

    @property
    def pote_sum(self) -> int:
        return sum(self.potes)


@dataclass(frozen=True)
class Imbalance:
    """One reason the teams are uneven.

    - `count`: `numbers` are the team sizes in team order, `expected` the sizes that are right.
    - `goal`: `numbers` are (line players in goal, line players placed), `expected` the (single)
      number of them the goal needs: only the ones beyond the spots in the teams keep goal.
    - `strength`: team `low_team` has the lowest (strongest) average pote and `high_team` the
      highest, `averages` being those two.
    - `pote`: pote `pote` has `high` players in team `high_team` and `low` in team `low_team`.
    """

    kind: Literal["count", "goal", "strength", "pote"]
    numbers: tuple[int, ...] = ()
    averages: tuple[float, ...] = ()
    expected: tuple[int, ...] = ()
    pote: int = 0
    high_team: int = 0
    high: int = 0
    low_team: int = 0
    low: int = 0


def assess(
    loads: Sequence[TeamLoad], in_goal: int = 0, *, team_size: int = TEAM_SIZE
) -> tuple[Imbalance, ...]:
    """Everything uneven about the teams, most visible first. Empty when they are fine.

    `in_goal` is how many line players keep goal: with the ones in the teams they are all the line
    players placed, and only those beyond the teams' spots (`team_size` each) should be there.
    """
    if not loads:
        return ()
    found: list[Imbalance] = []
    sizes = tuple(load.count for load in loads)
    expected = even_split(sum(sizes), len(loads))
    if sorted(sizes) != sorted(expected):
        found.append(Imbalance("count", numbers=sizes, expected=expected))
    placed = sum(sizes) + in_goal
    needed = goal_spots(placed, team_count=len(loads), team_size=team_size)
    if in_goal != needed:
        found.append(Imbalance("goal", numbers=(in_goal, placed), expected=(needed,)))

    average = {i: load.pote_sum / load.count for i, load in enumerate(loads) if load.count}
    if average:
        strongest, weakest = min(average, key=average.get), max(average, key=average.get)
        if average[weakest] - average[strongest] > POTE_AVERAGE_SPREAD:
            found.append(
                Imbalance(
                    "strength",
                    averages=(average[strongest], average[weakest]),
                    low_team=strongest,
                    high_team=weakest,
                )
            )

    found.extend(_pote_skews(loads))
    return tuple(found)


def _pote_skews(loads: Sequence[TeamLoad]) -> Iterable[Imbalance]:
    counts = [Counter(load.potes) for load in loads]
    for pote in sorted({p for load in loads for p in load.potes}):
        per_team = [c[pote] for c in counts]
        high, low = max(per_team), min(per_team)
        if high - low >= POTE_COUNT_SPREAD:
            yield Imbalance(
                "pote",
                pote=pote,
                high_team=per_team.index(high),
                high=high,
                low_team=per_team.index(low),
                low=low,
            )
