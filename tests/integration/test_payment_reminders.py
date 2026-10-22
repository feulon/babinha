"""Diretoria Avisos tab: e-mail a pending-payment notice to the players who owe."""

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from core.models import ClubSettings
from finance import reminders
from finance.models import Charge, ChargeKind, PaymentReminder, ReminderOutcome
from games import services
from games.models import GameDay
from tests.conftest import PIX_KEY, end_game

pytestmark = pytest.mark.django_db

URL = reverse("board:reminders")


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def owing(clock: FixedClock, club_values: ClubSettings, make_player, president: Player):
    """Garrincha (e-mail) and Didi (no e-mail) both joined the Sunday list and owe."""
    garrincha = make_player("Garrincha", referred_by=president, email="garrincha@example.com")
    didi = make_player("Didi", referred_by=president, email=None)
    day = services.current_game_day().pk
    services.join_list(day, garrincha)
    services.join_list(day, didi)
    end_game(GameDay.objects.get(pk=day))
    return garrincha, didi


def preview(client: Client, *players: Player):
    return client.post(URL, {"player": [p.pk for p in players]})


def send(client: Client, *players: Player, **extra: str):
    return client.post(URL, {"step": "send", "player": [p.pk for p in players], **extra})


class TestAccess:
    def test_a_player_cannot_open_it(self, client: Client, make_player, clock) -> None:
        client.force_login(make_player("Jogador"))
        assert client.get(URL).status_code == 403
        assert client.post(URL, {"step": "send"}).status_code == 403

    def test_anonymous_is_sent_to_login(self, client: Client) -> None:
        assert client.get(URL).status_code == 302


class TestList:
    def test_lists_debtors_with_amount_and_flags_missing_email(
        self, board_client: Client, owing
    ) -> None:
        html = board_client.get(URL).content.decode()
        assert "Garrincha" in html
        assert "Didi" in html
        assert "R$ 30,00" in html
        assert html.count("Sem e-mail") == 1
        assert html.count(" checked") == 2

    def test_nobody_owing(self, board_client: Client, clock) -> None:
        assert "Ninguém devendo" in board_client.get(URL).content.decode()

    def test_a_charge_under_review_is_not_a_debt(self, owing, board_client: Client) -> None:
        garrincha, _ = owing
        Charge.objects.filter(player=garrincha, kind=ChargeKind.GAME).update(status="em_analise")
        assert "Garrincha" not in board_client.get(URL).content.decode()


class TestPreview:
    def test_shows_the_text_with_amount_pix_and_link_and_sends_nothing(
        self, board_client: Client, owing
    ) -> None:
        garrincha, didi = owing
        html = preview(board_client, garrincha, didi).content.decode()
        assert "Olá, Garrincha!" in html
        assert "R$ 30,00" in html
        assert PIX_KEY in html
        assert "http://testserver/" in html
        assert "Sem e-mail: 1" in html
        assert mail.outbox == []

    def test_requires_a_choice(self, board_client: Client, owing) -> None:
        html = board_client.post(URL, {}).content.decode()
        assert "Escolha pelo menos um jogador" in html


class TestSend:
    def test_sends_to_players_with_email_and_reports_the_rest(
        self, board_client: Client, owing, board_member: Player
    ) -> None:
        garrincha, didi = owing
        html = send(board_client, garrincha, didi).content.decode()
        assert [m.to for m in mail.outbox] == [["garrincha@example.com"]]
        assert "R$ 30,00" in mail.outbox[0].body
        assert PIX_KEY in mail.outbox[0].body
        assert "Enviados: 1" in html
        assert "Sem e-mail: 1" in html
        assert "Falhas: 0" in html
        outcomes = {r.player.name: r.outcome for r in PaymentReminder.objects.all()}
        assert outcomes == {"Garrincha": ReminderOutcome.SENT, "Didi": ReminderOutcome.NO_EMAIL}
        assert PaymentReminder.objects.get(player=garrincha).sent_by == board_member

    def test_a_delivery_failure_is_reported_and_does_not_stop_the_others(
        self, board_client: Client, owing, make_player, president, monkeypatch
    ) -> None:
        garrincha, _ = owing
        zico = make_player("Zico", referred_by=president, email="zico@example.com")
        day = services.current_game_day().pk
        services.join_list(day, zico)
        end_game(GameDay.objects.get(pk=day))
        from django.core.mail import EmailMessage

        real = EmailMessage.send

        def flaky(self, *args, **kwargs):
            if self.to == ["garrincha@example.com"]:
                raise OSError("smtp down")
            return real(self, *args, **kwargs)

        monkeypatch.setattr(EmailMessage, "send", flaky)
        html = send(board_client, garrincha, zico).content.decode()
        assert "Falhas: 1" in html
        assert "Enviados: 1" in html
        assert [m.to for m in mail.outbox] == [["zico@example.com"]]
        assert PaymentReminder.objects.get(player=garrincha).outcome == ReminderOutcome.FAILED

    def test_only_players_who_owe_can_be_notified(
        self, board_client: Client, owing, board_member: Player
    ) -> None:
        response = send(board_client, board_member)
        assert response.status_code == 302
        assert mail.outbox == []

    def test_a_player_reminded_recently_is_flagged_and_skipped_by_default(
        self, board_client: Client, owing
    ) -> None:
        garrincha, _ = owing
        send(board_client, garrincha)
        assert "Avisado em" in board_client.get(URL).content.decode()
        assert "Já avisados nos últimos 3 dias" in preview(board_client, garrincha).content.decode()
        html = send(board_client, garrincha).content.decode()
        assert len(mail.outbox) == 1
        assert "Já avisados, ignorados: 1" in html

    def test_can_remind_again_on_purpose(self, board_client: Client, owing) -> None:
        garrincha, _ = owing
        send(board_client, garrincha)
        send(board_client, garrincha, repeat="1")
        assert len(mail.outbox) == 2

    def test_the_block_expires(self, board_client: Client, owing, clock: FixedClock) -> None:
        garrincha, _ = owing
        send(board_client, garrincha)
        PaymentReminder.objects.update(sent_at=clock.now() - reminders.RECENT_WINDOW * 2)
        send(board_client, garrincha)
        assert len(mail.outbox) == 2
