"""Bola de Ouro rules: who votes, when, and how three picks turn into a winner."""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

PLACES = (1, 2, 3)
POINTS = {1: 5, 2: 3, 3: 2}


class Phase(StrEnum):
    NONE = "none"  # no game to vote on (cancelled, no teams or no matches)
    WAITING = "waiting"  # the Sunday football has not ended
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class Window:
    phase: Phase
    opens_at: datetime | None = None
    closes_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.phase == Phase.OPEN


def window(
    *,
    now: datetime,
    closes_at: datetime,
    has_game: bool,
    matches: int,
    game_over_at: datetime | None = None,
    everyone_voted: bool = False,
) -> Window:
    """Voting opens when the Sunday is finished, never between individual matches.
    The result comes out on Monday when the next list opens (`closes_at`), even if everyone
    voted before. Only the demo supplies `everyone_voted` to preview the result sooner.
    """
    if not has_game or matches == 0:
        return Window(Phase.NONE)
    if now >= closes_at:
        return Window(Phase.CLOSED, game_over_at, closes_at)
    if game_over_at is None or now < game_over_at:
        return Window(Phase.WAITING, None, closes_at)
    if everyone_voted:
        return Window(Phase.CLOSED, game_over_at, closes_at)
    return Window(Phase.OPEN, game_over_at, closes_at)


class BallotError(Exception):
    """A ballot that cannot be counted. `message` is shown to the voter (pt-BR)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def validate_ballot(
    *, voter: int, picks: Mapping[int, int | None], eligible: Iterable[int]
) -> dict[int, int]:
    """The three picks by place, checked: all there, all different, nobody voting for themselves."""
    eligible_ids = set(eligible)
    if voter not in eligible_ids:
        raise BallotError("Só quem jogou neste domingo pode votar.")
    chosen: dict[int, int] = {}
    for place in PLACES:
        nominee = picks.get(place)
        if nominee is None:
            raise BallotError("Escolha os três jogadores: 1º, 2º e 3º lugar.")
        chosen[place] = nominee
    if len(set(chosen.values())) != len(PLACES):
        raise BallotError("Não repita o mesmo jogador em dois lugares.")
    if voter in chosen.values():
        raise BallotError("Você não pode votar em si mesmo.")
    if any(nominee not in eligible_ids for nominee in chosen.values()):
        raise BallotError("Só quem jogou neste domingo pode receber votos.")
    return chosen


@dataclass(frozen=True)
class Standing:
    player_id: int
    points: int
    places: tuple[int, int, int]  # how many 1st, 2nd and 3rd places

    @property
    def sort_key(self) -> tuple[int, int, int, int]:
        return (self.points, *self.places)


def standings(tallies: Iterable[tuple[int, int, int]]) -> tuple[Standing, ...]:
    """Best first. `tallies` are (player_id, place, votes). Ties on points are broken by the
    number of 1st places, then of 2nd places, then of 3rd places."""
    per_player: dict[int, Counter[int]] = {}
    for player_id, place, votes in tallies:
        per_player.setdefault(player_id, Counter())[place] += votes
    rows = [
        Standing(
            player_id,
            sum(POINTS[place] * votes for place, votes in counts.items()),
            (counts[1], counts[2], counts[3]),
        )
        for player_id, counts in per_player.items()
        if sum(counts.values()) > 0
    ]
    return tuple(sorted(rows, key=lambda s: (tuple(-v for v in s.sort_key), s.player_id)))


def winners(ranked: Sequence[Standing]) -> tuple[Standing, ...]:
    """Everyone who is still tied for first after the tie-breaks shares the Bola de Ouro."""
    if not ranked:
        return ()
    return tuple(row for row in ranked if row.sort_key == ranked[0].sort_key)
