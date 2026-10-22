from datetime import date, datetime
from decimal import Decimal

import pytest
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor

from accounts.models import Player
from core.clock import FixedClock, use_clock
from core.models import ClubSettings
from finance.charges import sync_game_charges
from finance.models import Charge, ChargeKind
from finance.monthly import join
from finance.services import refuse_receipt
from games import services as games
from games.models import GameDay, ListEntry
from tests.conftest import SP


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("debt_status", ["pendente", "recusada", "em_analise", "cancelada"])
def test_upgrade_preserves_existing_monthly_coverage(debt_status):
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [
        ("accounts", "0007_tutorial_progress"),
        ("games", "0002_game_day_finished"),
        ("finance", "0004_merge_reminder_and_charges_open"),
    ]
    executor.migrate(previous)
    try:
        old_apps = executor.loader.project_state(previous).apps
        player = old_apps.get_model("accounts", "Player").objects.create(
            name="Veterano", phone="11999999999", player_type="mensalista"
        )
        old_apps.get_model("accounts", "TutorialProgress").objects.create(player_id=player.pk)
        debtor = old_apps.get_model("accounts", "Player").objects.create(
            name="Veterano com dívida", phone="11777777777", player_type="mensalista"
        )
        old_apps.get_model("games", "GameDay").objects.create(date=date(2026, 9, 27))
        old_apps.get_model("games", "GameDay").objects.create(date=date(2026, 10, 11))
        for game in old_apps.get_model("games", "GameDay").objects.all():
            debt = old_apps.get_model("finance", "Charge").objects.create(
                player_id=debtor.pk,
                game_day_id=game.pk,
                kind="jogo",
                amount=30,
                reference_date=game.date,
                due_date=game.date,
                status=debt_status,
                created_at=datetime.combine(game.date, datetime.min.time(), tzinfo=SP),
            )
            if debt_status == "em_analise":
                old_apps.get_model("finance", "Receipt").objects.create(
                    charge_id=debt.pk,
                    submitted_by_id=debtor.pk,
                    file="old-receipt.png",
                    content_type="image/png",
                    size=100,
                )
        with use_clock(FixedClock(datetime(2026, 10, 15, 9, tzinfo=SP))):
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
        migrated = executor.loader.project_state(latest).apps
        assert (
            "accounts",
            "0012_remove_tutorial_progress",
        ) in executor.recorder.applied_migrations()
        enrollments = migrated.get_model("accounts", "MonthlyEnrollment").objects.filter(
            player_id=player.pk
        )
        assert set(enrollments.values_list("month", flat=True)) == {
            date(2026, 9, 1),
            date(2026, 10, 1),
        }
        assert all(e.enrolled_at.astimezone(SP).date() == e.month for e in enrollments)
        assert (
            migrated.get_model("accounts", "Player").objects.get(pk=player.pk).player_type
            == "avulso"
        )
        values = ClubSettings.load()
        values.single_fee = 30
        values.save()
        with transaction.atomic(), use_clock(FixedClock(datetime(2026, 10, 20, 9, tzinfo=SP))):
            director = Player.objects.create(name="Diretor", phone="11666666666", is_diretoria=True)
            debtor = Player.objects.get(pk=debtor.pk)
            newcomer = Player.objects.create(name="Novo", phone="11888888888")
            join(newcomer)
            for game in GameDay.objects.all():
                game.finished_at = datetime.combine(game.date, datetime.min.time(), tzinfo=SP)
                game.save()
                for member in (Player.objects.get(pk=player.pk), newcomer):
                    entry = ListEntry.objects.create(
                        game_day=game,
                        player=member,
                        position="linha",
                        status="lista",
                        joined_at=game.finished_at,
                        confirmed_at=game.finished_at,
                    )
                    sync_game_charges(game)
                    entry.status = "saiu"
                    entry.confirmed_at = None
                    entry.save()
                    sync_game_charges(game)
                    entry.status = "lista"
                    entry.confirmed_at = game.finished_at
                    entry.save()
                    sync_game_charges(game)
                assert not Charge.objects.filter(player_id=player.pk, game_day=game).exists()
                debt = Charge.objects.get(
                    player=newcomer, game_day=game, kind=ChargeKind.GAME, status="pendente"
                )
                sync_game_charges(game)
                debt.refresh_from_db()
                assert debt.status == "pendente"
                ListEntry.objects.create(
                    game_day=game,
                    player=debtor,
                    position="linha",
                    status="lista",
                    joined_at=game.finished_at,
                    confirmed_at=game.finished_at,
                )
                if debt_status == "em_analise":
                    debt = Charge.objects.get(player=debtor, game_day=game)
                    refuse_receipt(debt.receipts.get().pk, director, "Valor errado")
                    debt.refresh_from_db()
                    assert debt.status == "recusada"
                sync_game_charges(game)
                debt = Charge.objects.get(
                    player=debtor, game_day=game, status__in=["pendente", "recusada"]
                )
                original_pk = debt.pk
                sync_game_charges(game, [debtor.pk])
                debt.refresh_from_db()
                assert debt.is_payable
                assert debt.pk == original_pk
                games.leave_list(game.pk, debtor, board_actor=director)
                debt.refresh_from_db()
                assert debt.status == "cancelada"
                games.join_list(game.pk, debtor, board_actor=director)
                assert (
                    Charge.objects.filter(player=debtor, game_day=game, status="pendente").count()
                    == 1
                )
                games.cancel_game_day(game.pk, director, "Chuva")
                games.reopen_game_day(game.pk, director)
                assert (
                    Charge.objects.filter(player=debtor, game_day=game, status="pendente").count()
                    == 1
                )
                assert not Charge.objects.filter(player_id=player.pk, game_day=game).exists()

    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("old_first", "expected"), [("35.00", "35.00"), ("0.00", "50.00")], ids=["configured", "unset"]
)
def test_fine_base_keeps_the_configured_first_fine_or_defaults_to_50(old_first, expected):
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [("core", "0005_feedback")]
    executor.migrate(previous)
    try:
        old_apps = executor.loader.project_state(previous).apps
        old_apps.get_model("core", "ClubSettings").objects.create(
            pk=1, fine_first=old_first, fine_second="60.00", fine_third="90.00"
        )
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        assert ClubSettings.load().fine_base == Decimal(expected)
    finally:
        MigrationExecutor(connection).migrate(latest)
