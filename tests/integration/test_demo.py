from collections import Counter
from datetime import datetime
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import reverse

from accounts.models import Player
from community import selectors as community
from community import services as community_services
from community.domain.voting import Phase
from core import demo
from core.clock import FixedClock, use_clock
from demo import seed
from demo.players import DEMO_PASSWORD, PLAYERS
from finance.models import Charge, ChargeKind, ChargeStatus
from fines.models import Fine, FineReason, FineStatus
from games.domain.schedule import upcoming_sunday
from games.domain.types import EntryStatus
from games.models import GameDay, ListEntry
from matchday import services as matchday
from matchday.models import Lineup, Match
from tests.conftest import SP

# Any day and hour will do: a Tuesday morning, a Sunday afternoon and a Saturday night.
MOMENTS = [
    datetime(2026, 10, 13, 9, 0, tzinfo=SP),
    datetime(2026, 10, 18, 15, 30, tzinfo=SP),
    datetime(2026, 10, 17, 23, 50, tzinfo=SP),
]

# flush (used by --reset) needs real transactions.
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("demo_mode"),
]


@pytest.fixture
def demo_mode():
    with override_settings(BABA_DEMO=True):
        yield


def run_demo(*args: str) -> str:
    out = StringIO()
    call_command("demo", *args, stdout=out)
    return out.getvalue()


def sunday_of(now: datetime) -> GameDay:
    return GameDay.objects.get(date=upcoming_sunday(now))


def statuses(game_day: GameDay) -> Counter:
    return Counter(ListEntry.objects.filter(game_day=game_day).values_list("status", "position"))


class TestScenarios:
    # Each scenario is built once, the moments taking turns: every one of them is still used.
    @pytest.mark.parametrize(
        ("scenario", "moment"),
        [(s.key, MOMENTS[n % len(MOMENTS)]) for n, s in enumerate(seed.SCENARIOS)],
        ids=[s.key for s in seed.SCENARIOS],
    )
    def test_every_scenario_builds_at_any_day_and_hour(
        self, scenario: str, moment: datetime
    ) -> None:
        with use_clock(FixedClock(moment)):
            output = run_demo(scenario, "--reset")
        assert f"Situação '{scenario}' montada" in output
        assert Player.objects.count() == len(PLAYERS)
        assert Player.objects.get(is_presidente=True).check_password(DEMO_PASSWORD)

    def test_the_list_is_full_with_a_waitlist_and_priorities_applied(self) -> None:
        now = MOMENTS[0]
        with use_clock(FixedClock(now)):
            run_demo("lista")
            game_day = sunday_of(now)
        counts = statuses(game_day)
        assert counts[(EntryStatus.LISTA.value, "linha")] == 20
        assert counts[(EntryStatus.LISTA.value, "goleiro")] == 2
        assert counts[(EntryStatus.ESPERA.value, "linha")] == 2
        bumped = ListEntry.objects.filter(game_day=game_day, bumped=True)
        assert [e.player.nickname for e in bumped] == ["Sabiá"]

    def test_the_teams_are_drawn_then_confirmed(self) -> None:
        now = MOMENTS[0]
        with use_clock(FixedClock(now)):
            run_demo("times-sorteados")
            lineup = Lineup.objects.get(game_day=sunday_of(now))
            assert not lineup.is_confirmed
            run_demo("times-confirmados", "--reset")
            assert Lineup.objects.get(game_day=sunday_of(now)).is_confirmed

    def test_games_under_way_leave_one_on_court(self) -> None:
        now = MOMENTS[1]
        with use_clock(FixedClock(now)):
            run_demo("jogos-em-andamento")
            game_day = sunday_of(now)
            matches = list(Match.objects.filter(lineup__game_day=game_day).order_by("order"))
        assert [m.is_finished for m in matches] == [True, True, False]
        assert matches[2].goals.count() == 1
        # The waiting line: team 1 won and stayed, the draw sent both teams out.
        assert [(m.home_team.number, m.away_team.number) for m in matches] == [
            (1, 2),
            (1, 3),
            (4, 2),
        ]
        assert not game_day.is_finished

    def test_the_vote_opens_when_the_sunday_is_finished(self) -> None:
        now = MOMENTS[0]
        with use_clock(FixedClock(now)):
            run_demo("partida-encerrada")
            game_day = sunday_of(now)
            window = community.window_of(game_day)
            assert community.open_voting_day() == game_day
        assert window.phase == Phase.OPEN
        assert all(m.is_finished for m in Match.objects.filter(lineup__game_day=game_day))
        assert game_day.is_finished

    def test_the_last_vote_closes_the_bola_de_ouro_with_a_result(self) -> None:
        now = MOMENTS[1]
        with use_clock(FixedClock(now)):
            run_demo("bola-de-ouro-ultimo-voto")
            game_day = sunday_of(now)
            assert community.window_of(game_day).phase == Phase.OPEN
            president = Player.objects.get(is_presidente=True)
            nominees = [p for p in community.eligible_voters(game_day) if p != president]
            community_services.cast_vote(
                game_day.pk,
                president,
                first=nominees[0].pk,
                second=nominees[1].pk,
                third=nominees[2].pk,
            )
            assert community.window_of(game_day).phase == Phase.CLOSED
            assert community.gold_ball_winners([game_day])[game_day.pk]

    def test_the_vote_waits_while_a_game_is_unfinished(self) -> None:
        now = MOMENTS[1]
        with use_clock(FixedClock(now)):
            run_demo("jogos-em-andamento")
            window = community.window_of(sunday_of(now))
        assert window.phase == Phase.WAITING

    def test_the_fees_are_open_in_every_payment_state(self) -> None:
        now = MOMENTS[0]
        with use_clock(FixedClock(now)):
            run_demo("cobranca")
            game_day = sunday_of(now)
        assert game_day.is_finished
        games_fees = Charge.objects.filter(game_day=game_day, kind=ChargeKind.GAME)
        states = {c.status for c in games_fees}
        assert {
            ChargeStatus.PAID,
            ChargeStatus.PENDING,
            ChargeStatus.REFUSED,
            ChargeStatus.UNDER_REVIEW,
        } <= states
        assert Fine.objects.filter(
            game_day=game_day, reason=FineReason.NO_SHOW, status=FineStatus.PENDING
        ).exists()
        assert Fine.objects.filter(game_day=game_day, reason=FineReason.LATE).count() == 2

    def test_a_first_timer_is_drawn_with_whoever_invited_them(self) -> None:
        now = MOMENTS[0]
        with use_clock(FixedClock(now)):
            run_demo("borda-novato")
            lineup = Lineup.objects.get(game_day=sunday_of(now))
        team_of = dict(lineup.spots.values_list("player__nickname", "team_id"))
        assert team_of["Tatu"] == team_of["Fumaça"]

    def test_a_late_dropout_after_the_teams_costs_a_fine(self) -> None:
        now = MOMENTS[0]
        with use_clock(FixedClock(now)):
            run_demo("borda-desistencia-tardia")
            game_day = sunday_of(now)
        assert Fine.objects.filter(
            game_day=game_day, player__nickname="Formiga", reason=FineReason.LATE_DROPOUT
        ).exists()
        assert not matchday.in_sync(Lineup.objects.get(game_day=game_day))


class TestCommand:
    def test_listing_changes_nothing(self) -> None:
        output = run_demo("--listar")
        assert "lista" in output
        assert "cobranca" in output
        assert not Player.objects.exists()

    def test_an_existing_demo_needs_reset(self) -> None:
        run_demo("lista")
        with pytest.raises(CommandError, match="--reset"):
            run_demo("lista")
        run_demo("times-sorteados", "--reset")

    def test_empty_database_only_flag_leaves_existing_data(self) -> None:
        run_demo("lista")
        assert "nada a montar" in run_demo("cobranca", "--se-vazio")
        assert not Charge.objects.filter(game_day=sunday_of(datetime.now(SP))).exists()

    def test_refuses_to_wipe_real_accounts_without_force(self, make_player) -> None:
        make_player("Gente real")
        with pytest.raises(CommandError, match="--reset --force"):
            run_demo("lista", "--reset")
        run_demo("lista", "--reset", "--force")
        assert not Player.objects.filter(name="Gente real").exists()

    def test_unknown_scenario(self) -> None:
        with pytest.raises(CommandError, match="desconhecida"):
            run_demo("nao-existe")

    @override_settings(BABA_DEMO=False)
    def test_refuses_to_build_with_the_demo_off(self) -> None:
        with pytest.raises(CommandError, match="BABA_DEMO"):
            run_demo("lista")
        assert not Player.objects.exists()


class TestBanner:
    def test_every_screen_shows_the_banner_while_the_demo_is_on(self, client) -> None:
        run_demo("lista")
        for url in (reverse("accounts:login"), reverse("accounts:signup")):
            page = client.get(url).content.decode()
            assert demo.BANNER in page
        client.post(
            reverse("accounts:login"),
            {"login": "presidente@demo.baba", "password": DEMO_PASSWORD},
        )
        for name in ("games:home", "board:dashboard"):
            assert demo.BANNER in client.get(reverse(name), follow=True).content.decode()

    @override_settings(BABA_DEMO=False)
    def test_no_banner_when_the_demo_is_off(self, client) -> None:
        assert "data-demo-banner" not in client.get(reverse("accounts:login")).content.decode()
