"""The Sunday fee on the diretoria's screens, and the migration that moved it to the game's end."""

import importlib
from datetime import datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from core.models import ClubSettings
from finance.models import Charge, ChargeKind, ChargeStatus
from games import services
from games.models import GameDay
from tests.conftest import SP, end_game, start_game

pytestmark = pytest.mark.django_db

AFTER_KICKOFF = datetime(2026, 10, 18, 10, 30, tzinfo=SP)


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def signed_up(clock: FixedClock, club_values: ClubSettings, make_player) -> tuple[GameDay, Player]:
    game_day = services.current_game_day()
    player = make_player("Garrincha")
    services.join_list(game_day.pk, player)
    return game_day, player


def test_pending_page_is_empty_until_the_game_is_finished(
    signed_up, board_client: Client, clock: FixedClock
) -> None:
    game_day, _ = signed_up
    page = board_client.get(reverse("board:pending")).content.decode()
    assert "Garrincha" not in page

    clock.set(AFTER_KICKOFF)
    start_game()
    response = board_client.post(reverse("board:finish", kwargs={"day": game_day.date.isoformat()}))
    assert response.status_code == 302
    page = board_client.get(reverse("board:pending")).content.decode()
    assert "Garrincha" in page


def test_the_clock_never_charges_without_anyone_pressing_finish(
    signed_up, board_client: Client, clock: FixedClock
) -> None:
    game_day, player = signed_up
    clock.set(AFTER_KICKOFF)
    start_game()
    for moment in (
        datetime(2026, 10, 18, 12, 31, tzinfo=SP),
        datetime(2026, 10, 19, 9, 0, tzinfo=SP),
    ):
        clock.set(moment)
        page = board_client.get(reverse("board:pending")).content.decode()
        assert "Garrincha" not in page
    game_day.refresh_from_db()
    assert not game_day.is_finished
    assert not Charge.objects.filter(player=player, kind=ChargeKind.GAME).exists()


def test_player_sees_nothing_to_pay_after_joining(
    signed_up, make_player, clock: FixedClock
) -> None:
    game_day, player = signed_up
    client = Client()
    client.force_login(player)
    assert 'data-state="pagamento"' not in client.get(reverse("games:home")).content.decode()
    end_game(game_day)
    assert 'data-state="pagamento"' in client.get(reverse("games:home")).content.decode()


def test_migration_keeps_past_charges_and_cancels_unpaid_future_ones(
    signed_up, make_player, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from django.apps import apps

    migration = importlib.import_module("finance.migrations.0003_game_charges_open_after_the_game")
    game_day, player = signed_up
    paying = make_player("Pagante")
    services.join_list(game_day.pk, paying)
    now = clock.now()
    assert game_day.date > now.date()
    monkeypatch.setattr(migration.timezone, "now", clock.now)
    for who in (player, paying):
        Charge.objects.create(
            player=who,
            kind=ChargeKind.GAME,
            amount=30,
            reference_date=game_day.date,
            due_date=game_day.date,
            game_day=game_day,
            status=ChargeStatus.PAID if who == paying else ChargeStatus.PENDING,
            paid_at=now if who == paying else None,
            created_at=now,
        )
    GameDay.objects.filter(pk=game_day.pk).update(date=game_day.date + timedelta(days=28))
    migration.open_game_charges_after_the_game(apps, None)
    statuses = {c.player_id: c.status for c in Charge.objects.filter(kind=ChargeKind.GAME)}
    assert statuses == {player.pk: ChargeStatus.CANCELLED, paying.pk: ChargeStatus.PAID}
