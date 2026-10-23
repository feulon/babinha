"""Adjusting the drawn teams by hand: who is picked, and what a tap on the teams does with them.

The diretoria picks a player or a group (a unit: a group always moves whole), then taps a team to
move the unit there or another unit to swap places with it. Everything here is read-only; saving
goes through `matchday.services.assign`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from accounts.models import Player
from matchday.domain.balance import Imbalance, TeamLoad, assess
from matchday.domain.groups import Group
from matchday.domain.sizes import goal_full_message, goal_is_over_full
from matchday.grouping import Grouping
from matchday.selectors import Teams

GOAL = "gol"  # a line player drawn to complete the goal
NEW = "novo"  # on the list now, but not placed since the draw
SPLIT = "dividido"  # a group whose players are in different places
MOVE = "mover"
SWAP = "trocar"

Place = int | str  # a team number, or one of the above


@dataclass(frozen=True)
class Unit:
    """A line player alone, or a group that moves as one."""

    key: str
    players: tuple[Player, ...]
    group: Group | None
    place: Place

    @property
    def label(self) -> str:
        return " + ".join(player.display_name for player in self.players)


@dataclass(frozen=True)
class Row:
    """A line of a team: `unit` as it stands in that team (a split group shows only who is there).

    `swap` says a tap on the row swaps it with the picked unit.
    """

    unit: Unit
    label: str
    players: tuple[Player, ...]  # who of the unit is in this place, one line each
    picked: bool = False
    swap: bool = False


@dataclass(frozen=True)
class Spot:
    """A team (or the goal) with who is in it, and what the picked unit can do with it."""

    place: Place
    name: str
    rows: tuple[Row, ...]
    count: int
    pote_sum: int
    uneven: bool  # the team has not the number of players it should
    receives: bool  # the picked unit can be moved here
    suggested: bool  # ... and here is where it belongs (next to who it was invited by)


@dataclass(frozen=True)
class Bar:
    """A team in the balance strip: its pote sum, and how tall its bar is (percent of the strip)."""

    name: str
    pote_sum: int
    height: int

    @property
    def gap(self) -> int:
        """The empty part above the bar, for a drawing that grows from the bottom."""
        return 100 - self.height


@dataclass(frozen=True)
class Balance:
    """The teams' pote sums side by side, to see at a glance who is stronger."""

    bars: tuple[Bar, ...]
    spread: int  # the strongest team's lead over the weakest, in pote sum


@dataclass(frozen=True)
class Bond:
    """A first-timer tied to whoever invited them, for the diretoria to free."""

    rookie: Player
    inviter: Player


@dataclass(frozen=True)
class Board:
    """The teams as the diretoria sees and taps them."""

    spots: tuple[Spot, ...]  # the teams, in order
    goal: Spot
    pending: Spot  # who joined after the draw and has no team yet
    balance: Balance
    picked: Unit | None
    bonds: tuple[Bond, ...]  # the picked unit's first-timers who can be freed


def placement_of(state: Teams) -> dict[int, Place]:
    placement: dict[int, Place] = {}
    for roster in state.teams:
        placement.update({player.pk: roster.team.number for player in roster.players})
    placement.update({player.pk: GOAL for player in state.in_goal})
    placement.update({player.pk: NEW for player in state.unassigned})
    return placement


def _players_of(state: Teams) -> dict[int, Player]:
    everyone = [
        *(p for roster in state.teams for p in roster.players),
        *state.in_goal,
        *state.unassigned,
    ]
    return {player.pk: player for player in everyone}


def _place_order(place: Place) -> tuple[int, int]:
    if isinstance(place, int):
        return (0, place)
    return ({GOAL: 1, NEW: 2, SPLIT: 3}[place], 0)


def units_of(state: Teams, grouping: Grouping) -> tuple[Unit, ...]:
    """Everyone who can be moved, grouped as the rules require, in the order they are listed."""
    placement = placement_of(state)
    players = _players_of(state)
    units = []
    grouped: set[int] = set()
    for group in grouping.groups:
        members = [pid for pid in group.player_ids if pid in players]
        if len(members) < 2:
            continue
        grouped.update(members)
        places = {placement[pid] for pid in members}
        place = places.pop() if len(places) == 1 else SPLIT
        units.append(
            Unit(
                f"u{members[0]}",
                tuple(players[pid] for pid in members),
                group,
                place,
            )
        )
    units.extend(
        Unit(f"u{pid}", (player,), None, placement[pid])
        for pid, player in players.items()
        if pid not in grouped
    )
    return tuple(sorted(units, key=lambda u: (_place_order(u.place), u.label.casefold())))


def team_names(state: Teams) -> list[str]:
    return [roster.team.name for roster in state.teams]


def place_label(state: Teams, place: Place) -> str:
    if isinstance(place, int):
        return next(r.team.name for r in state.teams if r.team.number == place)
    return {GOAL: "No gol", NEW: "Entrou depois do sorteio", SPLIT: "Dividido"}[place]


def loads_of(state: Teams, placement: Mapping[int, Place]) -> list[TeamLoad]:
    players = _players_of(state)
    return [
        TeamLoad(
            tuple(
                players[pid].pote for pid, place in placement.items() if place == roster.team.number
            )
        )
        for roster in state.teams
    ]


def imbalances_of(
    state: Teams, placement: Mapping[int, Place] | None = None
) -> tuple[Imbalance, ...]:
    """How uneven the teams are, for the players placed in teams now (or in `placement`)."""
    placement = placement if placement is not None else placement_of(state)
    return assess(
        loads_of(state, placement), sum(1 for place in placement.values() if place == GOAL)
    )


def describe(imbalance: Imbalance, names: Sequence[str]) -> str:
    if imbalance.kind == "count":
        sizes = imbalance.numbers
        big, small = sizes.index(max(sizes)), sizes.index(min(sizes))
        right = " ou ".join(str(n) for n in sorted(set(imbalance.expected), reverse=True))
        return (
            f"{names[big]} tem {sizes[big]} e {names[small]} tem {sizes[small]} "
            f"(o certo é {right})."
        )
    if imbalance.kind == "goal":
        in_goal, placed = imbalance.numbers
        (needed,) = imbalance.expected
        need = (
            "ninguém precisa ir ao gol"
            if not needed
            else "1 de linha vai ao gol"
            if needed == 1
            else f"{needed} de linha vão ao gol"
        )
        have = (
            "ninguém está no gol"
            if not in_goal
            else f"{in_goal} {'está' if in_goal == 1 else 'estão'} no gol"
        )
        return f"Com {placed} de linha, {need}, mas {have}."
    if imbalance.kind == "strength":
        strong, weak = (f"{a:.1f}".replace(".", ",") for a in imbalance.averages)
        return (
            f"{names[imbalance.low_team]} é bem mais forte: pote médio {strong}, "
            f"contra {weak} do {names[imbalance.high_team]}."
        )
    return (
        f"Jogadores do pote {imbalance.pote}: {names[imbalance.high_team]} tem {imbalance.high} "
        f"e {names[imbalance.low_team]} tem {imbalance.low}."
    )


def split_lines(state: Teams, grouping: Grouping) -> list[str]:
    """The groups whose players are in different places, as "Rafa + Bruno"."""
    return [unit.label for unit in units_of(state, grouping) if unit.place == SPLIT]


def moved(placement: Mapping[int, Place], unit: Unit, place: Place) -> dict[int, Place]:
    return {**placement, **{player.pk: place for player in unit.players}}


def swapped(placement: Mapping[int, Place], first: Unit, second: Unit) -> dict[int, Place]:
    return {
        **placement,
        **{player.pk: second.place for player in first.players},
        **{player.pk: first.place for player in second.players},
    }


def _placed(placement: Mapping[int, Place]) -> int:
    """How many line players are in a team or in the goal."""
    return sum(1 for place in placement.values() if isinstance(place, int) or place == GOAL)


def goal_blocked(placement: Mapping[int, Place], after: Mapping[int, Place]) -> str:
    """Why `after` cannot be made because it sends more line players to the goal than it needs;
    empty when it can."""
    in_goal = [sum(1 for place in p.values() if place == GOAL) for p in (placement, after)]
    if goal_is_over_full(before=in_goal[0], after=in_goal[1], placed=_placed(after)):
        return goal_full_message(_placed(after))
    return ""


def _company(unit: Unit, placement: Mapping[int, Place]) -> Place | None:
    """The team a unit's players already in a team are in: where someone late should go."""
    placed = {placement[p.pk] for p in unit.players if isinstance(placement[p.pk], int)}
    return placed.pop() if len(placed) == 1 else None


def _picked(units: Sequence[Unit], key: str | None) -> Unit | None:
    """The unit asked for, or else whoever still has no place: it is the one to deal with."""
    return next((u for u in units if u.key == key), None) or next(
        (u for u in units if u.place in (NEW, SPLIT)), None
    )


def _bonds(unit: Unit | None, grouping: Grouping) -> tuple[Bond, ...]:
    if unit is None or unit.group is None:
        return ()
    free = {row.player.pk: row for row in grouping.rookie_rows if row.state == "applies"}
    return tuple(
        Bond(free[rookie].player, free[rookie].inviter)
        for rookie, _ in unit.group.bonds
        if rookie in free and any(p.pk == rookie for p in unit.players)
    )


def _rows(
    units: Sequence[Unit], picked: Unit | None, place: Place, placement: Mapping[int, Place]
) -> tuple[Row, ...]:
    """The units in `place`; a split group is in each of its places, with who is there."""
    rows = []
    for unit in units:
        here = [p for p in unit.players if placement[p.pk] == place]
        if not here:
            continue
        is_picked = picked is not None and unit.key == picked.key
        swap = (
            picked is not None
            and not is_picked
            and picked.place not in (NEW, SPLIT)
            and unit.place not in (NEW, SPLIT, picked.place)
        )
        rows.append(
            Row(unit, " + ".join(p.display_name for p in here), tuple(here), is_picked, swap)
        )
    return tuple(rows)


BAR_FLOOR = 25  # the shortest bar, so a team is never an empty stripe
BAR_MARGIN = 3  # bars start this far under the lowest sum, or the teams would all look the same


def balance_of(state: Teams) -> Balance:
    """The bars start a little under the lowest sum: a sum of 14 against 16 must show."""
    sums = [roster.pote_sum for roster in state.teams]
    if not sums:
        return Balance((), 0)
    floor = max(0, min(sums) - BAR_MARGIN)
    span = max(sums) - floor
    bars = tuple(
        Bar(
            roster.team.name,
            total,
            round(BAR_FLOOR + (100 - BAR_FLOOR) * (total - floor) / span) if span else 100,
        )
        for roster, total in zip(state.teams, sums, strict=True)
    )
    return Balance(bars, max(sums) - min(sums))


def board_for(state: Teams, grouping: Grouping, unit_key: str | None = None) -> Board:
    """The teams with the picked unit marked and, in each, what a tap does."""
    placement = placement_of(state)
    units = units_of(state, grouping)
    picked = _picked(units, unit_key)
    imbalances = imbalances_of(state, placement)
    uneven = any(i.kind == "count" for i in imbalances)
    company = _company(picked, placement) if picked and picked.place in (NEW, SPLIT) else None

    def receives(place: Place) -> bool:
        if picked is None or picked.place == place:
            return False
        return not (place == GOAL and goal_blocked(placement, moved(placement, picked, place)))

    def spot(place: Place, name: str, roster_players: Sequence[Player], pote_sum: int) -> Spot:
        return Spot(
            place,
            name,
            _rows(units, picked, place, placement),
            len(roster_players),
            pote_sum,
            uneven and place != GOAL,
            receives(place),
            place == company,
        )

    spots = tuple(spot(r.team.number, r.team.name, r.players, r.pote_sum) for r in state.teams)
    return Board(
        spots,
        spot(GOAL, place_label(state, GOAL), state.in_goal, 0),
        spot(NEW, "Entraram depois do sorteio", state.unassigned, 0),
        balance_of(state),
        picked,
        _bonds(picked, grouping),
    )
