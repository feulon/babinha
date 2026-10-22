"""The rules page, and who to ask for a new password when there is no e-mail."""

from decimal import Decimal

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.models import ClubSettings

pytestmark = pytest.mark.django_db

RULES = reverse("rules")
RESET = reverse("accounts:password_reset")


class TestRulesPage:
    def test_it_is_public_and_reads_the_values_from_the_settings(
        self, client: Client, club_values: ClubSettings
    ) -> None:
        response = client.get(RULES)
        html = response.content.decode()
        assert response.status_code == 200
        assert "Regras do babinha" in html
        assert "R$ 100,00 por mês" in html
        assert "vence todo dia 10" in html
        assert "R$ 30,00 por domingo" in html
        for fine in ("R$ 10,00", "R$ 15,00", "R$ 22,50"):
            assert fine in html

    def test_values_follow_a_change(self, client: Client, club_values: ClubSettings) -> None:
        club_values.single_fee = Decimal("45.00")
        club_values.save()
        assert "R$ 45,00 por domingo" in client.get(RULES).content.decode()

    def test_it_never_shows_the_pix_key(self, client: Client, club_values: ClubSettings) -> None:
        assert club_values.pix_key not in client.get(RULES).content.decode()


class TestPasswordRecoveryPage:
    def test_it_points_to_the_diretoria_without_naming_anyone(
        self, client: Client, president: Player, board_member: Player, make_player
    ) -> None:
        make_player("Zico")
        html = client.get(RESET).content.decode()
        assert 'data-state="sem-email"' in html
        assert "Peça uma nova senha à diretoria" in html
        for name in ("Presidente", "Diretor", "Zico"):
            assert name not in html

    def test_it_never_shows_the_board_whatsapp(
        self, client: Client, club_values: ClubSettings, president: Player
    ) -> None:
        club_values.board_contact_phone = "11987654321"
        club_values.save()
        html = client.get(RESET).content.decode()
        assert "wa.me" not in html
        assert "98765" not in html

    def test_sending_a_link_still_works(self, client: Client, make_player) -> None:
        make_player("Zico", email="zico@example.com")
        response = client.post(RESET, {"email": "zico@example.com"})
        assert response.status_code == 302
        assert len(mail.outbox) == 1
