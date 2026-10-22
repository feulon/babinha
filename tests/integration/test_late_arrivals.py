"""Late arrivals: marked by the diretoria during the games, fined at fixed values (15 min = R$ 10,
30 min = R$ 25) outside the 1st/2nd/3rd escalation, one fine per player per Sunday."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from core.models import ClubSettings
from finance.models import Charge, ChargeKind
from fines import services as fines
from fines.models import Fine, FineReason, FineStatus
from games import services as games
from games.domain.roster import ListError
from games.models import Attendance, ListEntry
from tests.conftest import SP, Sunday

pytestmark = pytest.mark.django_db

DAY = "2026-10-18"
BEFORE_KICKOFF = datetime(2026, 10, 18, 9, 55, tzinfo=SP)
AFTER_KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday], club_values) -> Sunday:
    return fill_sunday()


@pytest.fixture
def started(sunday: Sunday, clock: FixedClock) -> Sunday:
    clock.set(AFTER_KICKOFF)
    return sunday


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


def entry(sunday: Sunday, player: Player) -> ListEntry:
    return ListEntry.objects.get(game_day=sunday.game_day, player=player)


class TestMarkingLateness:
    def test_it_waits_for_the_kickoff_like_the_rest_of_attendance(
        self, sunday: Sunday, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(BEFORE_KICKOFF)
        with pytest.raises(ListError):
            games.mark_late(sunday.game_day.pk, sunday.line[0], 15, board_member)

    def test_arriving_late_means_being_present(self, started: Sunday, board_member: Player) -> None:
        player = started.line[0]
        games.mark_late(started.game_day.pk, player, 15, board_member)
        row = entry(started, player)
        assert (row.late_minutes, row.attendance) == (15, Attendance.PRESENTE)
        assert row.attendance_marked_by == board_member

    def test_a_mark_already_made_is_kept(self, started: Sunday, board_member: Player) -> None:
        player = started.line[0]
        games.mark_attendance(started.game_day.pk, player, Attendance.PRESENTE, board_member)
        marked_at = entry(started, player).attendance_marked_at
        games.mark_late(started.game_day.pk, player, 30, board_member)
        assert entry(started, player).attendance_marked_at == marked_at

    def test_only_the_two_tiers_are_valid_and_none_clears_it(
        self, started: Sunday, board_member: Player
    ) -> None:
        player = started.line[0]
        with pytest.raises(ListError):
            games.mark_late(started.game_day.pk, player, 20, board_member)
        games.mark_late(started.game_day.pk, player, 30, board_member)
        games.mark_late(started.game_day.pk, player, None, board_member)
        assert entry(started, player).late_minutes is None

    def test_someone_who_missed_cannot_be_late(self, started: Sunday, board_member: Player) -> None:
        player = started.line[0]
        games.mark_attendance(started.game_day.pk, player, Attendance.FALTOU, board_member)
        with pytest.raises(ListError):
            games.mark_late(started.game_day.pk, player, 15, board_member)

    def test_marking_a_no_show_drops_the_lateness(
        self, started: Sunday, board_member: Player
    ) -> None:
        player = started.line[0]
        games.mark_late(started.game_day.pk, player, 15, board_member)
        games.fine_late_arrivals(started.game_day.pk, board_member)
        games.mark_attendance(started.game_day.pk, player, Attendance.FALTOU, board_member)
        assert entry(started, player).late_minutes is None
        late = Fine.objects.get(player=player, reason=FineReason.LATE)
        assert late.status == FineStatus.VOIDED
        no_show = Fine.objects.get(player=player, reason=FineReason.NO_SHOW)
        assert no_show.status == FineStatus.PENDING
        assert (no_show.ordinal, no_show.amount) == (1, Decimal("10.00"))


class TestLateFines:
    def test_each_tier_has_its_fixed_value(self, started: Sunday, board_member: Player) -> None:
        quarter, half = started.line[:2]
        games.mark_late(started.game_day.pk, quarter, 15, board_member)
        games.mark_late(started.game_day.pk, half, 30, board_member)
        recorded = games.fine_late_arrivals(started.game_day.pk, board_member)
        assert {fine.player_id: fine.amount for fine in recorded} == {
            quarter.pk: Decimal("10.00"),
            half.pk: Decimal("25.00"),
        }
        charge = Charge.objects.get(fine=recorded[0])
        assert charge.kind == ChargeKind.FINE
        assert charge.amount == recorded[0].amount

    def test_the_values_follow_what_the_diretoria_configured(
        self, started: Sunday, board_member: Player
    ) -> None:
        ClubSettings.objects.update(late_fine_15=Decimal("12.50"), late_fine_30=Decimal("40.00"))
        quarter, half = started.line[:2]
        games.mark_late(started.game_day.pk, quarter, 15, board_member)
        games.mark_late(started.game_day.pk, half, 30, board_member)
        recorded = games.fine_late_arrivals(started.game_day.pk, board_member)
        assert {fine.player_id: fine.amount for fine in recorded} == {
            quarter.pk: Decimal("12.50"),
            half.pk: Decimal("40.00"),
        }

    def test_fining_twice_does_not_charge_twice(
        self, started: Sunday, board_member: Player
    ) -> None:
        games.mark_late(started.game_day.pk, started.line[0], 15, board_member)
        assert len(games.fine_late_arrivals(started.game_day.pk, board_member)) == 1
        assert games.fine_late_arrivals(started.game_day.pk, board_member) == []
        assert Fine.objects.filter(reason=FineReason.LATE).count() == 1

    def test_only_the_marked_are_fined(self, started: Sunday, board_member: Player) -> None:
        games.mark_attendance(
            started.game_day.pk, started.line[1], Attendance.PRESENTE, board_member
        )
        assert games.fine_late_arrivals(started.game_day.pk, board_member) == []

    def test_late_fines_stay_out_of_the_escalation(
        self, started: Sunday, board_member: Player
    ) -> None:
        player = started.line[0]
        games.mark_late(started.game_day.pk, player, 30, board_member)
        games.fine_late_arrivals(started.game_day.pk, board_member)
        assert fines.counted_occurrences(player) == 0
        assert fines.next_fine(player) == (1, Decimal("10.00"))
        assert fines.next_fines([player])[player.pk].ordinal == 1
        assert not Fine.objects.get(player=player).escalates

    def test_a_late_fine_is_not_the_first_occurrence_of_a_no_show(
        self, started: Sunday, board_member: Player
    ) -> None:
        player = started.line[0]
        games.mark_late(started.game_day.pk, player, 15, board_member)
        games.fine_late_arrivals(started.game_day.pk, board_member)
        games.mark_attendance(started.game_day.pk, player, Attendance.FALTOU, board_member)
        assert Fine.objects.get(player=player, reason=FineReason.NO_SHOW).ordinal == 1

    def test_changing_the_tier_voids_the_fine_so_it_can_be_redone(
        self, started: Sunday, board_member: Player
    ) -> None:
        player = started.line[0]
        games.mark_late(started.game_day.pk, player, 15, board_member)
        first = games.fine_late_arrivals(started.game_day.pk, board_member)[0]
        games.mark_late(started.game_day.pk, player, 30, board_member)
        first.refresh_from_db()
        assert first.status == FineStatus.VOIDED
        second = games.fine_late_arrivals(started.game_day.pk, board_member)
        assert [fine.amount for fine in second] == [Decimal("25.00")]

    def test_editing_the_price_does_not_void_a_pending_late_fine(
        self, started: Sunday, board_member: Player
    ) -> None:
        player = started.line[0]
        games.mark_late(started.game_day.pk, player, 15, board_member)
        fine = games.fine_late_arrivals(started.game_day.pk, board_member)[0]
        ClubSettings.objects.update(late_fine_15=Decimal("15.00"))
        assert fines.void_if_no_longer_due(fine, board_member) == 0
        fine.refresh_from_db()
        assert fine.status == FineStatus.PENDING
        assert fine.amount == Decimal("10.00")

    def test_a_paid_late_fine_freezes_the_mark(self, started: Sunday, board_member: Player) -> None:
        player = started.line[0]
        games.mark_late(started.game_day.pk, player, 15, board_member)
        fine = games.fine_late_arrivals(started.game_day.pk, board_member)[0]
        fines.mark_paid(fine, board_member)
        for change in (
            lambda: games.mark_late(started.game_day.pk, player, 30, board_member),
            lambda: games.mark_late(started.game_day.pk, player, None, board_member),
            lambda: games.mark_attendance(
                started.game_day.pk, player, Attendance.FALTOU, board_member
            ),
            lambda: games.mark_attendance(started.game_day.pk, player, "", board_member),
        ):
            with pytest.raises(ListError, match="já foi paga"):
                change()
        row = entry(started, player)
        assert (row.late_minutes, row.attendance) == (15, Attendance.PRESENTE)
        fine.refresh_from_db()
        assert fine.status == FineStatus.PAID
        assert not Fine.objects.filter(player=player, reason=FineReason.NO_SHOW).exists()
        games.mark_late(started.game_day.pk, player, 15, board_member)
        games.mark_attendance(started.game_day.pk, player, Attendance.PRESENTE, board_member)

    def test_clearing_the_mark_voids_the_fine(self, started: Sunday, board_member: Player) -> None:
        player = started.line[0]
        games.mark_late(started.game_day.pk, player, 15, board_member)
        fine = games.fine_late_arrivals(started.game_day.pk, board_member)[0]
        games.mark_late(started.game_day.pk, player, None, board_member)
        fine.refresh_from_db()
        assert fine.status == FineStatus.VOIDED

    def test_the_database_allows_one_active_late_fine_per_sunday(
        self, started: Sunday, board_member: Player
    ) -> None:
        player = started.line[0]
        games.mark_late(started.game_day.pk, player, 15, board_member)
        games.fine_late_arrivals(started.game_day.pk, board_member)
        with pytest.raises(IntegrityError), transaction.atomic():
            fines.record_late_fine(player, started.game_day, 30)

    def test_it_waits_for_the_kickoff(
        self, sunday: Sunday, board_member: Player, clock: FixedClock
    ) -> None:
        clock.set(BEFORE_KICKOFF)
        with pytest.raises(ListError):
            games.fine_late_arrivals(sunday.game_day.pk, board_member)


class TestLateScreens:
    def test_the_clock_button_cycles_through_the_tiers(
        self, started: Sunday, board_client: Client
    ) -> None:
        player = started.line[0]
        url = reverse("board:mark_late", kwargs={"day": DAY, "player_id": player.pk})
        for posted, expected in (("15", 15), ("30", 30), ("0", None)):
            response = board_client.post(url, {"minutes": posted}, HTTP_HX_REQUEST="true")
            assert response.status_code == 200
            assert entry(started, player).late_minutes == expected

    def test_a_bad_value_is_an_error_message_not_a_crash(
        self, started: Sunday, board_client: Client
    ) -> None:
        url = reverse("board:mark_late", kwargs={"day": DAY, "player_id": started.line[0].pk})
        html = board_client.post(url, {"minutes": "abc"}, HTTP_HX_REQUEST="true").content.decode()
        assert "Valor de atraso inválido." in html

    def test_the_fine_card_lists_who_is_late_and_what_it_costs(
        self, started: Sunday, board_client: Client, board_member: Player
    ) -> None:
        games.mark_late(started.game_day.pk, started.line[0], 15, board_member)
        games.mark_late(started.game_day.pk, started.line[1], 30, board_member)
        html = board_client.get(reverse("board:game_day", kwargs={"day": DAY})).content.decode()
        assert "Multar 2 jogadores" in html
        assert "R$ 35,00" in html
        response = board_client.post(reverse("board:fine_late", kwargs={"day": DAY}), follow=True)
        page = response.content.decode()
        assert "2 jogadores multados por atraso: R$ 35,00." in page
        assert "Todos os atrasos marcados já foram multados." in page
        assert "multado" in page

    def test_fining_with_nobody_late_says_so(self, started: Sunday, board_client: Client) -> None:
        response = board_client.post(reverse("board:fine_late", kwargs={"day": DAY}), follow=True)
        assert "Ninguém marcado com atraso ficou sem multa." in response.content.decode()

    def test_players_cannot_mark_or_fine_lateness(self, started: Sunday) -> None:
        client = Client()
        client.force_login(started.line[0])
        mark = reverse("board:mark_late", kwargs={"day": DAY, "player_id": started.line[0].pk})
        assert client.post(mark, {"minutes": "15"}).status_code == 403
        assert client.post(reverse("board:fine_late", kwargs={"day": DAY})).status_code == 403

    def test_the_fines_screen_shows_a_late_fine_without_an_occurrence_number(
        self, started: Sunday, board_client: Client, board_member: Player
    ) -> None:
        games.mark_late(started.game_day.pk, started.line[0], 15, board_member)
        games.fine_late_arrivals(started.game_day.pk, board_member)
        html = board_client.get(reverse("board:fines")).content.decode()
        assert "Atraso no jogo" in html
        assert "1ª ocorrência" not in html
