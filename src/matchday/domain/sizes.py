"""How many line players each team holds."""

TEAM_COUNT = 4
TEAM_SIZE = 5


def even_split(players: int, team_count: int = TEAM_COUNT) -> tuple[int, ...]:
    """`players` shared as evenly as possible: the bigger teams come first."""
    base, extra = divmod(players, team_count)
    return tuple(base + 1 if i < extra else base for i in range(team_count))


def team_sizes(
    line_players: int, *, team_count: int = TEAM_COUNT, team_size: int = TEAM_SIZE
) -> tuple[int, ...]:
    """Line players per team: full teams, or as even as possible when there are too few."""
    return even_split(min(line_players, team_count * team_size), team_count)


def goal_spots(
    line_players: int, *, team_count: int = TEAM_COUNT, team_size: int = TEAM_SIZE
) -> int:
    """Line players who have to keep goal: the ones beyond the spots in the teams."""
    return max(0, line_players - team_count * team_size)


def goal_is_over_full(*, before: int, after: int, placed: int) -> bool:
    """A change that takes the goal from `before` to `after` line players, with `placed` placed in
    teams or goal, puts more of them there than the goal needs. Leaving the goal alone, or with
    fewer, is never refused: it is how a goal that is too full gets fixed."""
    return after > before and after > goal_spots(placed)


def goal_full_message(placed: int) -> str:
    """Why nobody else can go to the goal when `placed` line players are placed (pt-BR)."""
    needed = goal_spots(placed)
    if not needed:
        return f"Com {placed} de linha ninguém precisa ir ao gol: os goleiros da lista bastam."
    if needed == 1:
        return f"Com {placed} de linha só 1 vai ao gol, e ele já está lá."
    return f"Com {placed} de linha só {needed} vão ao gol, e eles já estão lá."
