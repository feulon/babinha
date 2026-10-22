"""Match rules: seven minutes, or until a team scores twice. A draw at full time stands."""

from dataclasses import dataclass
from enum import StrEnum

MATCH_MINUTES = 7
GOALS_TO_WIN = 2


class Side(StrEnum):
    HOME = "home"
    AWAY = "away"


class Result(StrEnum):
    HOME_WIN = "home"
    AWAY_WIN = "away"
    DRAW = "draw"


class MatchOver(Exception):
    message = f"A partida já acabou: um time fez {GOALS_TO_WIN} gols."


@dataclass(frozen=True)
class Score:
    home: int = 0
    away: int = 0

    def __post_init__(self) -> None:
        if self.home < 0 or self.away < 0:
            raise ValueError("goals cannot be negative")
        if self.home > GOALS_TO_WIN or self.away > GOALS_TO_WIN:
            raise ValueError(f"no team scores more than {GOALS_TO_WIN} goals")
        if self.home == self.away == GOALS_TO_WIN:
            raise ValueError("the match ends when the first team reaches the goal limit")

    @property
    def decided(self) -> bool:
        """A team reached the goal limit, so the match is over before the time runs out."""
        return max(self.home, self.away) >= GOALS_TO_WIN

    @property
    def result(self) -> Result:
        if self.home > self.away:
            return Result.HOME_WIN
        if self.away > self.home:
            return Result.AWAY_WIN
        return Result.DRAW

    def add(self, side: Side) -> "Score":
        if self.decided:
            raise MatchOver
        if side == Side.HOME:
            return Score(self.home + 1, self.away)
        return Score(self.home, self.away + 1)
