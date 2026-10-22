"""Injectable clock. All schedule rules read the current time through `now()`.

Production uses the system clock. Tests swap it with `use_clock(FixedClock(...))`. For manual
testing, `core.fake_clock` can install a `ShiftedClock` at startup (see BABA_FAKE_NOW).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=SAO_PAULO)


@dataclass
class FixedClock:
    """A clock frozen at `current` that tests can move forward explicitly."""

    current: datetime = field(default_factory=lambda: datetime.now(tz=SAO_PAULO))

    def __post_init__(self) -> None:
        if self.current.tzinfo is None:
            raise ValueError("FixedClock needs an aware datetime")

    def now(self) -> datetime:
        return self.current.astimezone(SAO_PAULO)

    def set(self, current: datetime) -> None:
        if current.tzinfo is None:
            raise ValueError("FixedClock needs an aware datetime")
        self.current = current

    def advance(self, delta: timedelta) -> None:
        self.current = self.current + delta


class ShiftedClock:
    """Real time moved by a fixed offset: reads `start` when created and runs normally after."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("ShiftedClock needs an aware datetime")
        self.offset = start - datetime.now(tz=SAO_PAULO)

    def now(self) -> datetime:
        return datetime.now(tz=SAO_PAULO) + self.offset


_clock: Clock = SystemClock()


def now() -> datetime:
    """Current time in America/Sao_Paulo."""
    return _clock.now().astimezone(SAO_PAULO)


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> None:
    """Replace the process-wide clock. Only for startup; tests use `use_clock`."""
    global _clock
    _clock = clock


@contextmanager
def use_clock(clock: Clock) -> Iterator[Clock]:
    global _clock
    previous = _clock
    _clock = clock
    try:
        yield clock
    finally:
        _clock = previous
