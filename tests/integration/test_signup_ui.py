"""Sign-up: the 11-digit phone limit, the rules acknowledgement and the CSP of the page."""

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core import clock

pytestmark = pytest.mark.django_db

SIGNUP = reverse("accounts:signup")


def test_the_csp_allows_the_local_preview_image(client: Client) -> None:
    csp = client.get(SIGNUP)["Content-Security-Policy"]
    assert "img-src 'self' data: blob:" in csp
    assert "unsafe-inline" not in csp


def test_more_than_11_digits_are_refused_by_the_server(client: Client, president: Player) -> None:
    data = {
        "name": "Zico",
        "phone": "(11) 98765-43210",
        "email": "zico@example.com",
        "password": "gol1",
        "password_confirm": "gol1",
        "accept_rules": "on",
    }
    response = client.post(SIGNUP, data)
    assert response.status_code == 200
    assert "phone" in response.context["form"].errors
    assert not Player.objects.filter(name="Zico").exists()


class TestRulesAcknowledgement:
    @staticmethod
    def data(**extra: str) -> dict[str, str]:
        return {
            "name": "Zico",
            "phone": "(11) 98765-4321",
            "email": "zico@example.com",
            "password": "gol1",
            "password_confirm": "gol1",
            "accept_rules": "on",
            **extra,
        }

    def test_the_rules_text_follows_the_settings(
        self, client: Client, president: Player, club_values
    ) -> None:
        club_values.fine_base = Decimal("40.00")
        club_values.fine_factor = Decimal("1.25")
        club_values.late_fine_15 = Decimal("12.00")
        club_values.late_fine_30 = Decimal("27.50")
        club_values.monthly_fee = Decimal("150.00")
        club_values.monthly_due_day = 5
        club_values.single_fee = Decimal("40.00")
        club_values.save()
        html = client.get(SIGNUP).content.decode()
        for text in (
            "R$ 40,00, sobe 25% a cada nova",
            "Exemplo de faltas: R$ 40,00 → R$ 50,00 → R$ 62,50 → R$ 78,13...",
            "<dt>Atraso de 15 minutos</dt><dd>R$ 12,00</dd>",
            "<dt>Atraso de 30 minutos</dt><dd>R$ 27,50</dd>",
            "<dd>R$ 150,00</dd>",
            "vencimento: todo dia 5",
            "<dd>R$ 40,00</dd>",
        ):
            assert text in html
        assert "R$ 20,00" not in html

    def test_the_cards_come_in_order_and_drop_the_forgiveness_line(
        self, client: Client, president: Player
    ) -> None:
        html = client.get(SIGNUP).content.decode()
        order = [
            html.index(f'data-signup-rule="{key}"')
            for key in ("jogo", "lista", "prioridade", "valores", "multas")
        ]
        assert order == sorted(order)
        assert "perdão" not in html
        assert "perdoa" not in html
        assert "Pagar não apaga o histórico." in html

    def test_signup_is_blocked_without_the_acknowledgement(
        self, client: Client, president: Player
    ) -> None:
        payload = self.data()
        del payload["accept_rules"]
        response = client.post(SIGNUP, payload)
        assert response.status_code == 200
        assert "accept_rules" in response.context["form"].errors
        assert not Player.objects.filter(name="Zico").exists()

    def test_signup_completes_and_records_when_it_was_accepted(
        self, client: Client, president: Player
    ) -> None:
        before = clock.now()
        response = client.post(SIGNUP, self.data())
        assert response.status_code == 302
        player = Player.objects.get(name="Zico")
        assert player.rules_accepted_at is not None
        assert player.rules_accepted_at >= before

    def test_existing_players_have_no_acceptance(self, president: Player) -> None:
        assert president.rules_accepted_at is None

    def test_the_invite_link_still_prefills_and_completes(
        self, client: Client, president: Player, settings
    ) -> None:
        settings.BABA_INVITE_CODE = "bola-9"
        url = f"{SIGNUP}?indicacao=Zico+da+Silva&codigo=bola-9"
        form = client.get(url).context["form"]
        assert form.initial == {"referrer_name": "Zico da Silva", "invite_code": "bola-9"}
        response = client.post(url, self.data(invite_code="bola-9", referrer_name="Zico da Silva"))
        assert response.status_code == 302
        assert Player.objects.get(name="Zico").referrer_text == "Zico da Silva"
