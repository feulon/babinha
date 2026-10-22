"""Fitting groups of players into teams of limited size.

A group never splits, so dealing teams with groups is first a packing problem: every group of two
or more must land whole in one team. Groups are few (at most about ten, in four teams), so the
search is exact.
"""

import random
from collections.abc import Sequence


def place_groups(
    sizes: Sequence[int], capacities: Sequence[int], rng: random.Random
) -> list[int] | None:
    """A random way to fit groups of `sizes` into teams of `capacities`: one team index per group.

    None when they cannot all fit.
    """
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i], rng.random()))
    room = list(capacities)
    placed = [-1] * len(sizes)

    def place(k: int) -> bool:
        if k == len(order):
            return True
        group = order[k]
        teams = [t for t in range(len(room)) if room[t] >= sizes[group]]
        rng.shuffle(teams)
        tried: set[int] = set()
        for team in teams:
            if room[team] in tried:  # same room left: a team just like one already tried
                continue
            tried.add(room[team])
            room[team] -= sizes[group]
            placed[group] = team
            if place(k + 1):
                return True
            room[team] += sizes[group]
        return False

    return placed if place(0) else None


def fits(sizes: Sequence[int], capacities: Sequence[int]) -> bool:
    return place_groups(sizes, capacities, random.Random(0)) is not None


def oversized_capacities(capacities: Sequence[int], sizes: Sequence[int]) -> tuple[int, ...]:
    """The capacities with the biggest team grown to hold the biggest group.

    The players it takes in are taken out of the other teams, the biggest first, so the number
    of players on teams stays the same: 5 · 5 · 5 · 5 becomes 6 · 5 · 5 · 4 for a group of 6.
    """
    ordered = sorted(capacities, reverse=True)
    needed = max(sizes, default=0)
    if not ordered or needed <= ordered[0]:
        return tuple(ordered)
    extra = needed - ordered[0]
    ordered[0] = needed
    for _ in range(extra):
        donor = max(range(1, len(ordered)), key=lambda t: ordered[t], default=None)
        if donor is None or ordered[donor] == 0:
            break
        ordered[donor] -= 1
    return tuple(sorted(ordered, reverse=True))
