from collections.abc import Callable

from django.http import HttpRequest

from community import selectors


def gold_ball(request: HttpRequest) -> dict[str, Callable[[], bool]]:
    """`gold_ball_open` is true while a Bola de Ouro vote is open. A callable, so the queries run
    only on the screens that show the badge."""
    return {"gold_ball_open": lambda: selectors.open_voting_day() is not None}
