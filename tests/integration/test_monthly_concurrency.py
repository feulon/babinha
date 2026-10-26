from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from queue import Queue
from threading import Event
from time import monotonic, sleep

import pytest
from django.db import connection, transaction

from core.clock import FixedClock
from finance import charges, monthly
from finance.models import Charge, ChargeKind, ChargeStatus
from games import services as games
from games.models import GameDay
from tests.conftest import SP, start_game


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("enrollment_first", [False, True])
def test_enrollment_and_finalization_serialize(
    clock: FixedClock, club_values, make_player, president, enrollment_first
) -> None:
    assert connection.vendor == "postgresql"
    player = make_player(referred_by=president)
    game = games.current_game_day()
    games.join_list(game.pk, player)
    before_cutoff = datetime(2026, 10, 18, 12, 29, 59, tzinfo=SP)
    cutoff = datetime(2026, 10, 18, 12, 30, tzinfo=SP)
    clock.set(before_cutoff if enrollment_first else cutoff)
    start_game(game)
    ready = Event()
    release = Event()
    second_pid = Queue()

    def assess() -> None:
        games.finish_game_day(game.pk, president)

    first_action, second_action = (
        (lambda: monthly.join(player), assess)
        if enrollment_first
        else (assess, lambda: monthly.join(player))
    )

    def first() -> None:
        try:
            with transaction.atomic():
                first_action()
                ready.set()
                assert release.wait(timeout=30)
        finally:
            connection.close()

    def second() -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                second_pid.put(cursor.fetchone()[0])
            second_action()
        finally:
            connection.close()

    blocked = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(first)
        try:
            assert ready.wait(timeout=5)
            clock.set(cutoff)
            second_result = pool.submit(second)
            pid = second_pid.get(timeout=5)
            deadline = monotonic() + 5
            while monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT cardinality(pg_blocking_pids(%s)) > 0", [pid])
                    blocked = cursor.fetchone()[0]
                if blocked or second_result.done():
                    break
                sleep(0.01)
        finally:
            release.set()
        first_result.result(timeout=10)
        second_result.result(timeout=10)

    assert blocked
    enrollment = player.enrollments.get()
    assert enrollment.month == date(2026, 10, 1)
    assert enrollment.enrolled_at == (before_cutoff if enrollment_first else cutoff)
    monthly_charge = Charge.objects.get(player=player, kind=ChargeKind.MONTHLY)
    assert monthly_charge.status == ChargeStatus.PENDING
    assert monthly_charge.due_date == date(2026, 10, 10)

    def sunday_debts() -> list[int]:
        return list(
            Charge.objects.filter(player=player, game_day=game, kind=ChargeKind.GAME)
            .exclude(status=ChargeStatus.CANCELLED)
            .values_list("pk", flat=True)
        )

    debts = sunday_debts()
    assert len(debts) == (0 if enrollment_first else 1)
    with transaction.atomic():
        charges.sync_game_charges(GameDay.objects.select_for_update().get(pk=game.pk))
    assert sunday_debts() == debts
    games.cancel_game_day(game.pk, president, "Chuva")
    games.reopen_game_day(game.pk, president)
    assert len(sunday_debts()) == (0 if enrollment_first else 1)
    games.leave_list(game.pk, player, board_actor=president)
    games.join_list(game.pk, player, board_actor=president)
    assert len(sunday_debts()) == (0 if enrollment_first else 1)
