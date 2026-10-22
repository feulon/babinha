"""The season table: one metric picks the order, the others break ties."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from community.domain.stats import Totals


class Metric(StrEnum):
    GOLD_BALLS = "bolas"
    GOALS = "gols"
    ASSISTS = "assistencias"
    SUNDAYS = "presenca"
    WINS = "vitorias"


DEFAULT_METRIC = Metric.GOLD_BALLS
_TIE_BREAKS = (Metric.GOLD_BALLS, Metric.GOALS, Metric.ASSISTS, Metric.WINS, Metric.SUNDAYS)
_ATTRIBUTE = {
    Metric.GOLD_BALLS: "gold_balls",
    Metric.GOALS: "goals",
    Metric.ASSISTS: "assists",
    Metric.SUNDAYS: "sundays",
    Metric.WINS: "wins",
}


def value(totals: Totals, metric: Metric) -> int:
    return getattr(totals, _ATTRIBUTE[metric])


@dataclass(frozen=True)
class Row:
    position: int
    player_id: int
    totals: Totals


def rank(table: Mapping[int, Totals], by: Metric, names: Mapping[int, str]) -> tuple[Row, ...]:
    """Best first: `by` decides, the other metrics break ties, then the name. Only players equal
    on every metric share a position (1, 2, 2, 4). Players with nothing at all are left out."""
    order = [by, *(m for m in _TIE_BREAKS if m != by)]

    def key(player_id: int) -> tuple:
        row = table[player_id]
        return (*(-value(row, m) for m in order), names[player_id].casefold(), player_id)

    ids = sorted((p for p, row in table.items() if row.sundays or row.gold_balls), key=key)
    rows: list[Row] = []
    for index, player_id in enumerate(ids):
        previous = rows[-1] if rows else None
        same = previous is not None and all(
            value(previous.totals, m) == value(table[player_id], m) for m in order
        )
        position = previous.position if same and previous else index + 1
        rows.append(Row(position, player_id, table[player_id]))
    return tuple(rows)
