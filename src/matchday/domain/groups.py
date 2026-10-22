"""Players who must be drawn into the same team.

Two things tie players together on a Sunday: a group the diretoria made by hand, and a
first-timer's bond with whoever invited them. Both are edges of one graph; a group is a connected
component of it, so a chain (a first-timer invited by another first-timer) is a single group.

Pure functions: `matchday.grouping` loads who is tied to whom and `matchday.domain.draw` deals
each group as one unit.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Group:
    """Two or more line players who play in the same team."""

    player_ids: tuple[int, ...]  # sorted
    bonds: tuple[tuple[int, int], ...]  # (first-timer, who invited them) inside the group
    manual_ids: tuple[int, ...]  # ids of the diretoria's groups merged into this one

    @property
    def size(self) -> int:
        return len(self.player_ids)

    @property
    def rookie_ids(self) -> frozenset[int]:
        return frozenset(rookie for rookie, _ in self.bonds)


def rookie_bonds(
    playing_ids: Iterable[int],
    inviters: Mapping[int, int | None],
    rookies: Iterable[int],
    released: Iterable[int] = (),
) -> dict[int, int]:
    """The active first-timer bonds: first-timer id -> id of who invited them.

    A bond holds only when both play (`playing_ids` are the line players on the list), the player
    is a first-timer and the diretoria did not release them. A missing inviter, or one who is a
    goalkeeper or off the list, leaves the first-timer free.
    """
    playing = set(playing_ids)
    free = set(released)
    return {
        rookie: inviter
        for rookie in rookies
        if rookie in playing
        and rookie not in free
        and (inviter := inviters.get(rookie)) is not None
        and inviter in playing
        and inviter != rookie
    }


def build_groups(
    manual_groups: Mapping[int, Sequence[int]],
    bonds: Mapping[int, int],
    playing_ids: Iterable[int],
) -> tuple[Group, ...]:
    """The groups of two or more among `playing_ids`, ordered by their smallest player id.

    `manual_groups` maps the diretoria's group id to its members. Members who are not playing are
    ignored, so a group shrinks (or dissolves) when someone leaves the list.
    """
    playing = set(playing_ids)
    parent = {pid: pid for pid in playing}

    def find(pid: int) -> int:
        while parent[pid] != pid:
            parent[pid] = parent[parent[pid]]
            pid = parent[pid]
        return pid

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    for members in manual_groups.values():
        here = [pid for pid in members if pid in playing]
        for other in here[1:]:
            union(here[0], other)
    active_bonds = {r: i for r, i in bonds.items() if r in playing and i in playing}
    for rookie, inviter in active_bonds.items():
        union(rookie, inviter)

    components: dict[int, list[int]] = {}
    for pid in sorted(playing):
        components.setdefault(find(pid), []).append(pid)

    groups = []
    for members in components.values():
        if len(members) < 2:
            continue
        inside = set(members)
        manual_ids = tuple(
            gid
            for gid, ids in sorted(manual_groups.items())
            if sum(1 for pid in ids if pid in inside) >= 2
        )
        group_bonds = tuple(sorted((r, i) for r, i in active_bonds.items() if r in inside))
        groups.append(Group(tuple(members), group_bonds, manual_ids))
    return tuple(sorted(groups, key=lambda group: group.player_ids[0]))
