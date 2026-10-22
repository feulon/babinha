"""Simultaneous sign-ups must never exceed the spot limits."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connection

from accounts.models import Player, Position
from core.clock import FixedClock
from games import services
from games.domain.types import EntryStatus
from games.models import ListEntry


@pytest.mark.django_db(transaction=True)
def test_parallel_joins_respect_the_limits(
    clock: FixedClock, make_player: Callable[..., Player]
) -> None:
    game_day = services.current_game_day()
    players = [make_player() for _ in range(30)] + [
        make_player(position=Position.GOLEIRO) for _ in range(6)
    ]
    barrier = Barrier(len(players))

    def join(player: Player) -> None:
        try:
            barrier.wait()
            services.join_list(game_day.pk, player)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=len(players)) as pool:
        list(pool.map(join, players))

    confirmed = ListEntry.objects.filter(game_day=game_day, status=EntryStatus.LISTA)
    line = confirmed.filter(position=Position.LINHA).count()
    goalkeepers = confirmed.filter(position=Position.GOLEIRO).count()
    assert goalkeepers == 4
    assert line == 20
    assert ListEntry.objects.filter(game_day=game_day).count() == len(players)
