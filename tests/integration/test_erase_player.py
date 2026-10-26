"""manage.py apagar_jogador: erasing a player and all their data for good (LGPD)."""

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from io import StringIO

import pytest
from django.contrib.sessions.models import Session
from django.core.files.storage import storages
from django.core.management import CommandError, call_command
from django.db import models
from django.test import Client

from accounts import erasure, photos
from accounts.management.commands import apagar_jogador
from accounts.models import MonthlyEnrollment, Player, current_month
from community import services as community
from community.models import Ballot
from core.clock import FixedClock
from core.models import ClubSettings, Feedback, FeedbackKind
from finance import services as payments
from finance.models import Charge, Receipt
from games import services as games
from games.models import GameDay, ListEntry
from matchday import services as matchday
from matchday.domain.score import Side
from matchday.models import Goal, LineupSpot, Match, MatchPlayer
from tests.conftest import PDF_BYTES, PNG_BYTES, SP, Sunday, end_game, start_game

pytestmark = pytest.mark.django_db

KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)
AFTER_GAME = datetime(2026, 10, 18, 11, 30, tzinfo=SP)
NEXT_WEEK = datetime(2026, 10, 20, 9, 0, tzinfo=SP)


@dataclass
class Scene:
    sunday: Sunday
    board: Player
    target: Player  # scored twice in the first match, assisted in the second, won the Bola de Ouro
    referred: Player  # signed up naming the target
    match: Match
    files: tuple[str, ...]

    @property
    def game_day(self) -> GameDay:
        return self.sunday.game_day


def team(game_day: GameDay, number: int) -> list[Player]:
    spots = LineupSpot.objects.filter(lineup__game_day=game_day, team__number=number)
    return [spot.player for spot in spots.select_related("player").order_by("pk")]


@pytest.fixture
def scene(
    fill_sunday: Callable[..., Sunday],
    board_member: Player,
    make_player: Callable[..., Player],
    club_values: ClubSettings,
    clock: FixedClock,
    receipt_upload,
    president: Player,
) -> Scene:
    sunday = fill_sunday()
    day_id = sunday.game_day.pk
    matchday.draw(day_id, board_member, random.Random(3))
    matchday.confirm(day_id, board_member)
    clock.set(KICKOFF)
    start_game(sunday.game_day)
    one, two = team(sunday.game_day, 1), team(sunday.game_day, 2)
    target = one[0]
    first = matchday.add_match(day_id, board_member, home_team=1, away_team=2)
    matchday.add_goal(first.pk, board_member, side=Side.HOME, scorer=target.pk, assist=one[1].pk)
    matchday.add_goal(first.pk, board_member, side=Side.AWAY, scorer=two[0].pk)
    matchday.add_goal(first.pk, board_member, side=Side.HOME, scorer=target.pk)
    second = matchday.add_match(day_id, board_member, home_team=1, away_team=3)
    matchday.add_goal(second.pk, board_member, side=Side.HOME, scorer=one[1].pk, assist=target.pk)
    matchday.finish_match(second.pk, board_member)
    clock.set(AFTER_GAME)
    end_game(sunday.game_day)
    for voter in sunday.line[1:4]:
        others = [p for p in sunday.line if p not in (voter, target)]
        community.cast_vote(day_id, voter, first=target.pk, second=others[0].pk, third=others[1].pk)
    community.cast_vote(
        day_id, target, first=sunday.line[1].pk, second=sunday.line[2].pk, third=sunday.line[3].pk
    )

    charge = Charge.objects.get(player=target)
    receipt = payments.submit_receipt(charge.pk, target, receipt_upload(PDF_BYTES, "pix.pdf"))
    payments.approve_receipt(receipt.pk, president)
    MonthlyEnrollment.objects.create(player=target, month=current_month())
    Feedback.objects.create(author=target, kind=FeedbackKind.BUG, text="O botão some.")
    photos.replace(target, PNG_BYTES)
    referred = make_player("Indicado", referred_by=target)
    Client().force_login(target)
    clock.set(NEXT_WEEK)  # the Bola de Ouro is out
    target.refresh_from_db()
    return Scene(sunday, board_member, target, referred, first, (target.photo, receipt.file.name))


def run(phone: str, *flags: str) -> str:
    out = StringIO()
    call_command("apagar_jogador", phone, *flags, stdout=out)
    return out.getvalue()


def snapshot() -> dict[str, int]:
    """Row counts of every table the erasure can touch, plus who points at whom."""
    counts = {
        model._meta.label: model._default_manager.count()
        for model in {ref.model for ref in erasure.DELETED + erasure.CLEARED + erasure.HANDED_OVER}
    }
    counts["sessions"] = Session.objects.count()
    counts["referrals"] = Player.objects.exclude(referred_by=None).count()
    counts["assists"] = Goal.objects.exclude(assist=None).count()
    return counts


def stored(name: str) -> bool:
    return storages["receipts"].exists(name)


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Pretend the command runs in a terminal where the operator types `answer`."""

    def answer(typed: str) -> None:
        monkeypatch.setattr(apagar_jogador, "is_interactive", lambda: True)
        monkeypatch.setattr(apagar_jogador, "ask", lambda prompt: typed)

    return answer


class TestDryRun:
    def test_prints_what_would_go_and_changes_nothing(self, scene: Scene) -> None:
        before = snapshot()
        out = run(scene.target.phone)

        assert snapshot() == before
        assert Player.objects.filter(pk=scene.target.pk).exists()
        assert all(stored(name) for name in scene.files)
        assert "scripts/baba.sh backup" in out
        assert scene.target.name in out
        assert "gols marcados: 2" in out
        assert "cobranças: 1" in out
        assert "comprovantes: 1" in out
        assert "votos dados (registro de que votou): 1" in out
        assert "arquivos no armazenamento (foto e comprovantes): 2" in out
        assert "jogadores indicados por ele: 1" in out
        assert "assistências em gols de outros: 1" in out
        assert "Simulação: nada foi apagado" in out

    def test_shows_the_effects_on_what_stays(self, scene: Scene) -> None:
        out = run(scene.target.phone)
        assert "2 x 1 passa a 0 x 1" in out
        assert "Caixa: R$ 30,00" in out
        assert "Ranking: ele sai da tabela" in out
        assert "1 assistências de outros jogadores em gols dele" in out
        assert "Bola de Ouro: Domingo 18/10/2026 era dele; fica com" in out


class TestErase:
    def test_removes_every_row_and_file_and_keeps_everyone_else(
        self, scene: Scene, interactive: Callable[[str], None]
    ) -> None:
        target = scene.target
        others = Player.objects.exclude(pk=target.pk).count()
        interactive(target.phone)

        out = run(target.phone, "--confirmar")

        assert f"Jogador {target.name} apagado." in out
        assert not Player.objects.filter(pk=target.pk).exists()
        assert Player.objects.count() == others
        for ref in erasure.DELETED + erasure.CLEARED + erasure.HANDED_OVER:
            assert not ref.model._default_manager.filter(**{ref.lookup: target.pk}).exists()
        assert not any(stored(name) for name in scene.files)
        scene.referred.refresh_from_db()
        assert scene.referred.referred_by is None
        sessions = [s.get_decoded().get("_auth_user_id") for s in Session.objects.all()]
        assert str(target.pk) not in sessions
        # The rest of the Sunday is still there: the other goal, the matches, the other votes.
        assert Goal.objects.filter(match=scene.match).count() == 1
        assert Match.objects.filter(lineup__game_day=scene.game_day).count() == 2
        assert Ballot.objects.filter(game_day=scene.game_day).count() == 3
        assert ListEntry.objects.filter(game_day=scene.game_day).count() == 21
        assert MatchPlayer.objects.exists()

    def test_what_a_diretor_did_passes_to_the_presidente(
        self, scene: Scene, president: Player, interactive: Callable[[str], None]
    ) -> None:
        goals = Goal.objects.count()
        interactive(scene.board.phone)
        out = run(scene.board.phone, "--confirmar")

        assert "passa para o presidente" in out
        assert Goal.objects.count() == goals
        assert set(Goal.objects.values_list("created_by", flat=True)) == {president.pk}
        assert scene.game_day.lineup.drawn_by == president
        assert Receipt.objects.get().reviewed_by == president

    def test_logs_only_the_id_and_time(
        self, scene: Scene, interactive: Callable[[str], None], caplog: pytest.LogCaptureFixture
    ) -> None:
        interactive(scene.target.phone)
        with caplog.at_level(logging.INFO, logger=apagar_jogador.__name__):
            run(scene.target.phone, "--confirmar")
        (record,) = [r for r in caplog.records if r.name == apagar_jogador.__name__]
        expected = f"Jogador {scene.target.pk} apagado em 2026-10-20T09:00:00-03:00"
        assert record.getMessage() == expected

    def test_a_failing_step_rolls_everything_back(
        self, scene: Scene, interactive: Callable[[str], None], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = snapshot()
        forget = erasure._forget_logins
        calls = []

        def fail_the_real_run(player: Player) -> None:
            calls.append(player.pk)
            forget(player)
            if len(calls) == 2:  # the first call is the dry-run that is shown before confirming
                raise RuntimeError("disk full")

        monkeypatch.setattr(erasure, "_forget_logins", fail_the_real_run)
        interactive(scene.target.phone)
        with pytest.raises(RuntimeError):
            run(scene.target.phone, "--confirmar")

        assert snapshot() == before
        assert Player.objects.filter(pk=scene.target.pk).exists()
        assert all(stored(name) for name in scene.files)

    def test_a_file_that_cannot_be_deleted_is_reported(
        self, scene: Scene, interactive: Callable[[str], None], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        storage = storages["receipts"]
        delete = storage.delete
        photo, receipt = scene.files

        def flaky(name: str) -> None:
            if name == receipt:
                raise OSError("bucket offline")
            delete(name)

        monkeypatch.setattr(storage, "delete", flaky)
        interactive(scene.target.phone)
        with pytest.raises(CommandError, match="1 arquivos ficaram") as error:
            run(scene.target.phone, "--confirmar")

        assert receipt in str(error.value)
        assert not Player.objects.filter(pk=scene.target.pk).exists()
        assert not stored(photo)


class TestConfirmation:
    def test_a_wrong_phone_erases_nothing(
        self, scene: Scene, interactive: Callable[[str], None]
    ) -> None:
        interactive("11 99999-0000")
        with pytest.raises(CommandError, match="não confere"):
            run(scene.target.phone, "--confirmar")
        assert Player.objects.filter(pk=scene.target.pk).exists()

    def test_any_formatting_of_the_phone_confirms(
        self, scene: Scene, interactive: Callable[[str], None]
    ) -> None:
        digits = scene.target.phone
        interactive(f"({digits[:2]}) {digits[2:7]}-{digits[7:]}")
        run(digits, "--confirmar")
        assert not Player.objects.filter(pk=scene.target.pk).exists()

    def test_without_a_terminal_it_refuses_unless_sure(self, scene: Scene) -> None:
        with pytest.raises(CommandError, match="--sim-tenho-certeza"):
            run(scene.target.phone, "--confirmar")
        assert Player.objects.filter(pk=scene.target.pk).exists()

        run(scene.target.phone, "--confirmar", "--sim-tenho-certeza")
        assert not Player.objects.filter(pk=scene.target.pk).exists()

    def test_sure_alone_does_not_erase(self, scene: Scene) -> None:
        out = run(scene.target.phone, "--sim-tenho-certeza")
        assert "Simulação" in out
        assert Player.objects.filter(pk=scene.target.pk).exists()


class TestRefusals:
    def test_the_presidente(self, president: Player) -> None:
        with pytest.raises(CommandError, match="presidente"):
            run(president.phone, "--confirmar", "--sim-tenho-certeza")
        assert Player.objects.filter(pk=president.pk).exists()

    @pytest.mark.parametrize("phone", ["11988887777", "123"])
    def test_an_unknown_or_invalid_phone(self, president: Player, phone: str) -> None:
        with pytest.raises(CommandError, match="Nenhum jogador"):
            run(phone)

    def test_a_player_on_a_coming_sundays_list(
        self, clock: FixedClock, make_player: Callable[..., Player], president: Player
    ) -> None:
        player = make_player(referred_by=president)
        games.join_list(games.current_game_day().pk, player)
        with pytest.raises(CommandError, match="Tire ele da lista"):
            run(player.phone, "--confirmar", "--sim-tenho-certeza")
        assert Player.objects.filter(pk=player.pk).exists()


def test_every_reference_to_a_player_is_handled() -> None:
    """A new foreign key to Player must be added to one of the erasure lists."""
    handled = {
        ref.model._meta.get_field(ref.lookup.split("__")[0])
        for ref in erasure.DELETED + erasure.CLEARED + erasure.HANDED_OVER
    }
    missing = {
        f"{rel.related_model._meta.label}.{rel.field.name}"
        for rel in Player._meta.get_fields(include_hidden=True)
        if isinstance(rel, models.ForeignObjectRel)
        and rel.field not in handled
        and rel.field not in erasure.CASCADED
        and not rel.related_model._meta.auto_created  # the groups and permissions tables
    }
    assert missing == set()
