from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError

from accounts.models import Player, PlayerType, Position
from core.clock import FixedClock
from core.models import ClubSettings
from fines import services as fines
from fines.models import Fine, FineReason, FineStatus
from games import services
from games.domain.roster import GameCancelled, ListError, NotOpenYet
from games.domain.types import EntryStatus
from games.models import Attendance, GameDay, ListEntry
from tests.conftest import SP

SUNDAY = date(2026, 10, 18)
SUNDAY_MORNING = datetime(2026, 10, 18, 8, 0, tzinfo=SP)
AFTER_KICKOFF = datetime(2026, 10, 18, 10, 30, tzinfo=SP)

pytestmark = pytest.mark.django_db


@pytest.fixture
def values() -> ClubSettings:
    settings = ClubSettings.load()
    settings.fine_base = Decimal("10.00")
    settings.save()
    return settings


@pytest.fixture
def game_day(clock: FixedClock) -> GameDay:
    game_day = services.current_game_day()
    assert game_day.date == SUNDAY
    return game_day


def status_of(game_day: GameDay, player: Player) -> str:
    return ListEntry.objects.get(game_day=game_day, player=player).status


def fill_line(game_day: GameDay, make_player: Callable[..., Player], n: int, **kw) -> list:
    players = [make_player(**kw) for _ in range(n)]
    for player in players:
        services.join_list(game_day.pk, player)
    return players


class TestJoinAndLeave:
    def test_join_persists_the_entry(self, game_day: GameDay, make_player) -> None:
        player = make_player()
        result = services.join_list(game_day.pk, player)
        entry = ListEntry.objects.get(game_day=game_day, player=player)
        assert entry.status == EntryStatus.LISTA
        assert entry.position == Position.LINHA
        assert entry.confirmed_at is not None
        assert len(result.events) == 1

    def test_full_list_goes_to_the_waitlist(self, game_day: GameDay, make_player) -> None:
        fill_line(game_day, make_player, 22)
        late = make_player()
        services.join_list(game_day.pk, late)
        assert status_of(game_day, late) == EntryStatus.ESPERA

    def test_mensalista_bumps_and_waitlist_refills(
        self, game_day: GameDay, make_player, clock: FixedClock
    ) -> None:
        avulsos = fill_line(game_day, make_player, 22)
        mensalista = make_player(player_type=PlayerType.MENSALISTA)
        services.join_list(game_day.pk, mensalista)
        bumped = ListEntry.objects.get(game_day=game_day, player=avulsos[-1])
        assert bumped.status == EntryStatus.ESPERA
        assert bumped.bumped
        services.leave_list(game_day.pk, avulsos[0])
        promoted = ListEntry.objects.get(game_day=game_day, player=avulsos[-1])
        assert promoted.status == EntryStatus.LISTA
        assert promoted.promoted

    def test_agregado_cannot_join_before_wednesday(self, game_day: GameDay, make_player) -> None:
        agregado = make_player(player_type=PlayerType.AGREGADO)
        with pytest.raises(NotOpenYet):
            services.join_list(game_day.pk, agregado)
        assert not ListEntry.objects.filter(player=agregado).exists()

    def test_free_dropout_records_no_fine(self, game_day: GameDay, make_player) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        result = services.leave_list(game_day.pk, player)
        assert result.fines == ()
        assert status_of(game_day, player) == EntryStatus.SAIU

    def test_cancelled_sunday_blocks_sign_ups(
        self, game_day: GameDay, make_player, board_member: Player
    ) -> None:
        services.cancel_game_day(game_day.pk, board_member, "Chuva")
        with pytest.raises(GameCancelled):
            services.join_list(game_day.pk, make_player())

    def test_only_board_can_act_for_others(self, game_day: GameDay, make_player) -> None:
        player, other = make_player(), make_player()
        with pytest.raises(PermissionDenied):
            services.join_list(game_day.pk, player, board_actor=other)

    def test_board_removal_after_kickoff_is_not_fined(
        self, game_day: GameDay, make_player, board_member: Player, clock: FixedClock, values
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        clock.set(AFTER_KICKOFF)
        result = services.leave_list(game_day.pk, player, board_actor=board_member)
        assert result.fines == ()

    def test_database_rejects_inconsistent_entries(self, game_day: GameDay, make_player) -> None:
        with pytest.raises(IntegrityError):
            ListEntry.objects.create(
                game_day=game_day,
                player=make_player(),
                position=Position.LINHA,
                status=EntryStatus.LISTA,
                joined_at=SUNDAY_MORNING,
                confirmed_at=None,
            )

    def test_game_day_must_be_a_sunday(self) -> None:
        with pytest.raises(IntegrityError):
            GameDay.objects.create(date=date(2026, 10, 17))


class TestFines:
    def late_dropout(self, game_day: GameDay, player: Player, clock: FixedClock) -> Fine:
        clock.set(SUNDAY_MORNING - timedelta(days=5))
        services.join_list(game_day.pk, player)
        clock.set(SUNDAY_MORNING)
        result = services.leave_list(game_day.pk, player)
        assert len(result.fines) == 1
        return result.fines[0]

    def test_late_dropout_is_fined(
        self, game_day: GameDay, make_player, clock: FixedClock, values
    ) -> None:
        fine = self.late_dropout(game_day, make_player(), clock)
        assert fine.reason == FineReason.LATE_DROPOUT
        assert fine.ordinal == 1
        assert fine.amount == Decimal("10.00")
        assert fine.status == FineStatus.PENDING

    def test_escalation_and_forgiveness(
        self, make_player, clock: FixedClock, values, board_member: Player
    ) -> None:
        player = make_player()
        sundays = [services.game_day_for(SUNDAY + timedelta(weeks=i)) for i in range(4)]
        recorded = []
        for week, game_day in enumerate(sundays[:3]):
            clock.set(SUNDAY_MORNING + timedelta(weeks=week) - timedelta(days=5))
            services.join_list(game_day.pk, player)
            clock.set(SUNDAY_MORNING + timedelta(weeks=week))
            recorded += services.leave_list(game_day.pk, player).fines
        assert [(f.ordinal, f.amount) for f in recorded] == [
            (1, Decimal("10.00")),
            (2, Decimal("15.00")),
            (3, Decimal("22.50")),
        ]

        forgiven = fines.forgive(recorded[1].pk, board_member, "Avisou no grupo")
        assert forgiven.status == FineStatus.FORGIVEN
        assert forgiven.resolved_by == board_member
        assert forgiven.resolution_note == "Avisou no grupo"
        assert fines.counted_occurrences(player) == 2

        # The count never resets, but the forgiven occurrence no longer counts.
        clock.set(SUNDAY_MORNING + timedelta(weeks=3) - timedelta(days=5))
        services.join_list(sundays[3].pk, player)
        clock.set(SUNDAY_MORNING + timedelta(weeks=3))
        (fourth,) = services.leave_list(sundays[3].pk, player).fines
        assert fourth.ordinal == 3
        assert fourth.amount == Decimal("22.50")

    def test_forgiving_needs_a_reason(
        self, game_day: GameDay, make_player, clock: FixedClock, board_member: Player
    ) -> None:
        fine = self.late_dropout(game_day, make_player(), clock)
        with pytest.raises(fines.FineError):
            fines.forgive(fine.pk, board_member, "   ")

    def test_cannot_forgive_twice(
        self, game_day: GameDay, make_player, clock: FixedClock, board_member: Player
    ) -> None:
        fine = self.late_dropout(game_day, make_player(), clock)
        fines.forgive(fine.pk, board_member, "ok")
        with pytest.raises(fines.FineError):
            fines.forgive(fine.pk, board_member, "de novo")

    def test_value_is_fixed_when_recorded(
        self, game_day: GameDay, make_player, clock: FixedClock, values
    ) -> None:
        fine = self.late_dropout(game_day, make_player(), clock)
        values.fine_base = Decimal("99.00")
        values.save()
        fine.refresh_from_db()
        assert fine.amount == Decimal("10.00")

    def test_cancelling_the_sunday_voids_its_fines(
        self, game_day: GameDay, make_player, clock: FixedClock, board_member: Player
    ) -> None:
        player = make_player()
        fine = self.late_dropout(game_day, player, clock)
        voided = services.cancel_game_day(game_day.pk, board_member, "Campo interditado")
        assert voided == 1
        fine.refresh_from_db()
        assert fine.status == FineStatus.VOIDED
        assert fines.counted_occurrences(player) == 0

    def test_reopening_refills_but_keeps_fines_voided(
        self, game_day: GameDay, make_player, clock: FixedClock, board_member: Player
    ) -> None:
        fill_line(game_day, make_player, 22)
        waiting = make_player()
        services.join_list(game_day.pk, waiting)
        services.cancel_game_day(game_day.pk, board_member, "Chuva")
        first = ListEntry.objects.filter(game_day=game_day, status="lista").first()
        services.leave_list(game_day.pk, first.player, board_actor=board_member)
        services.reopen_game_day(game_day.pk, board_member)
        assert status_of(game_day, waiting) == EntryStatus.LISTA

    def test_no_show_from_attendance(
        self,
        game_day: GameDay,
        make_player,
        clock: FixedClock,
        board_member: Player,
        values,
    ) -> None:
        player = make_player()
        services.join_list(game_day.pk, player)
        with pytest.raises(ListError, match="início do jogo"):
            services.mark_attendance(game_day.pk, player, Attendance.FALTOU, board_member)

        clock.set(AFTER_KICKOFF)
        fine = services.mark_attendance(game_day.pk, player, Attendance.FALTOU, board_member)
        assert fine is not None
        assert fine.reason == FineReason.NO_SHOW
        assert fine.amount == Decimal("10.00")

        # Marking "faltou" again does not duplicate the fine.
        again = services.mark_attendance(game_day.pk, player, Attendance.FALTOU, board_member)
        assert again is None
        assert Fine.objects.filter(player=player).count() == 1

        # Correcting to present voids it.
        services.mark_attendance(game_day.pk, player, Attendance.PRESENTE, board_member)
        fine.refresh_from_db()
        assert fine.status == FineStatus.VOIDED

    def test_attendance_only_for_people_on_the_list(
        self, game_day: GameDay, make_player, clock: FixedClock, board_member: Player
    ) -> None:
        outsider = make_player()
        clock.set(AFTER_KICKOFF)
        with pytest.raises(ListError, match="não está na lista"):
            services.mark_attendance(game_day.pk, outsider, Attendance.FALTOU, board_member)
