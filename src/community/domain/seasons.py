"""Seasons are cut by the day the diretoria ends them: that day still belongs to the old one."""

from collections.abc import Sequence
from datetime import date


def season_index(day: date, ends: Sequence[date]) -> int:
    """Which season `day` falls in, given the end dates of the finished seasons in order.

    0 is the first season; `len(ends)` is the one still open.
    """
    for index, end in enumerate(ends):
        if day <= end:
            return index
    return len(ends)
