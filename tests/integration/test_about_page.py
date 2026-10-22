"""The "Sobre o app" tab: the creator's contact and the bug / suggestion form."""

from collections.abc import Callable
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from core.models import Feedback, FeedbackKind
from tests.conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db

ABOUT = reverse("about")
FEEDBACK = reverse("feedback")
ADMIN_LIST = reverse("admin:core_feedback_changelist")


@pytest.fixture
def signed_in(client: Client, make_player: Callable[..., Player]) -> Client:
    client.force_login(make_player("Zeca"))
    return client


class TestAboutPage:
    def test_it_asks_for_login(self, client: Client) -> None:
        response = client.get(ABOUT)
        assert response.status_code == 302
        assert response.url.startswith(reverse("accounts:login"))

    def test_the_creator_contact_comes_from_the_settings(self, signed_in: Client, settings) -> None:
        settings.BABA_CREATOR_EMAIL = "criador@example.com"
        html = signed_in.get(ABOUT).content.decode()
        assert 'data-about="contato"' in html
        assert 'href="mailto:criador@example.com"' in html

    def test_without_a_creator_email_there_is_no_contact(self, signed_in: Client, settings) -> None:
        settings.BABA_CREATOR_EMAIL = ""
        response = signed_in.get(ABOUT)
        assert response.status_code == 200
        html = response.content.decode()
        assert 'data-about="contato"' not in html
        assert "mailto:" not in html

    def test_sending_asks_for_login(self, client: Client) -> None:
        response = client.post(FEEDBACK, {"kind": "bug", "text": "quebrou"})
        assert response.status_code == 302
        assert not Feedback.objects.exists()

    def test_the_feedback_page_asks_for_login(self, client: Client) -> None:
        response = client.get(FEEDBACK)
        assert response.status_code == 302
        assert response.url.startswith(reverse("accounts:login"))

    def test_a_valid_message_is_saved_and_confirmed(
        self, signed_in: Client, clock: FixedClock
    ) -> None:
        response = signed_in.post(
            FEEDBACK, {"kind": "sugestao", "text": "  Tabela de artilheiros  "}
        )
        assert response.status_code == 302
        saved = Feedback.objects.get()
        assert saved.author.name == "Zeca"
        assert saved.kind == FeedbackKind.SUGGESTION
        assert saved.text == "Tabela de artilheiros"
        assert saved.created_at == clock.now()
        assert "Mensagem enviada" in signed_in.get(FEEDBACK).content.decode()

    @pytest.mark.parametrize("text", ["", "   \n "])
    def test_an_empty_text_is_refused(self, signed_in: Client, text: str) -> None:
        response = signed_in.post(FEEDBACK, {"kind": "bug", "text": text})
        assert response.status_code == 200
        assert "Escreva a mensagem" in response.content.decode()
        assert not Feedback.objects.exists()

    def test_a_missing_or_unknown_kind_is_refused(self, signed_in: Client) -> None:
        for data in ({"text": "oi"}, {"kind": "outro", "text": "oi"}):
            response = signed_in.post(FEEDBACK, data)
            assert response.status_code == 200
        assert not Feedback.objects.exists()

    def test_a_text_over_the_limit_is_refused(self, signed_in: Client) -> None:
        text = "a" * (Feedback.MAX_TEXT_LENGTH + 1)
        response = signed_in.post(FEEDBACK, {"kind": "bug", "text": text})
        assert "Passou de" in response.content.decode()
        assert not Feedback.objects.exists()

    def test_the_text_at_the_limit_is_accepted(self, signed_in: Client) -> None:
        text = "a" * Feedback.MAX_TEXT_LENGTH
        signed_in.post(FEEDBACK, {"kind": "bug", "text": text})
        assert Feedback.objects.get().text == text

    def test_the_form_is_protected_by_csrf(self, make_player: Callable[..., Player]) -> None:
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(make_player("Zeca"))
        response = strict.post(FEEDBACK, {"kind": "bug", "text": "quebrou"})
        assert response.status_code == 403
        assert not Feedback.objects.exists()

    def test_sending_too_many_in_an_hour_is_blocked_then_allowed_again(
        self, signed_in: Client, clock: FixedClock
    ) -> None:
        for n in range(5):
            signed_in.post(FEEDBACK, {"kind": "bug", "text": f"bug {n}"})
        response = signed_in.post(FEEDBACK, {"kind": "bug", "text": "mais um"})
        assert response.status_code == 429
        assert "Tente de novo" in response.content.decode()
        assert Feedback.objects.count() == 5
        clock.set(clock.now() + timedelta(hours=1, minutes=1))
        signed_in.post(FEEDBACK, {"kind": "bug", "text": "agora vai"})
        assert Feedback.objects.count() == 6

    def test_the_limit_is_per_player(
        self, signed_in: Client, make_player: Callable[..., Player]
    ) -> None:
        for n in range(5):
            signed_in.post(FEEDBACK, {"kind": "bug", "text": f"bug {n}"})
        other = Client()
        other.force_login(make_player("Outro"))
        other.post(FEEDBACK, {"kind": "bug", "text": "o meu passa"})
        assert Feedback.objects.count() == 6


class TestReadingTheMessages:
    def test_a_superuser_reads_them_in_the_admin(
        self, client: Client, make_player: Callable[..., Player]
    ) -> None:
        creator = make_player("Criador", is_superuser=True)
        Feedback.objects.create(author=creator, kind="bug", text="Botão <b>torto</b>")
        client.force_login(creator)
        response = client.get(ADMIN_LIST)
        assert response.status_code == 200
        assert "Botão &lt;b&gt;torto&lt;/b&gt;" in response.content.decode()

    def test_the_presidente_the_diretoria_and_players_get_a_403(
        self,
        signed_in: Client,
        president: Player,
        board_member: Player,
    ) -> None:
        Feedback.objects.create(author=president, kind="bug", text="segredo do criador")
        players = [signed_in, Client(), Client()]
        players[1].force_login(president)
        players[2].force_login(board_member)
        for browser in players:
            for url in (ADMIN_LIST, reverse("admin:index"), reverse("admin:login")):
                response = browser.get(url, follow=True)
                assert response.status_code == 403
                assert "segredo do criador" not in response.content.decode()

    def test_an_anonymous_visitor_is_sent_to_the_app_login(self, client: Client) -> None:
        response = client.get(ADMIN_LIST, follow=True)
        assert response.redirect_chain[-1][0].startswith(reverse("accounts:login"))

    def test_the_admin_login_does_not_take_passwords(
        self, client: Client, make_player: Callable[..., Player]
    ) -> None:
        creator = make_player("Criador", is_superuser=True, phone="11999990000")
        response = client.post(
            reverse("admin:login"), {"username": "11999990000", "password": TEST_PASSWORD}
        )
        assert response.status_code == 302
        assert response.url.startswith(reverse("accounts:login"))
        assert "_auth_user_id" not in client.session
        chain = client.get(ADMIN_LIST, follow=True).redirect_chain
        assert chain[-1][0].startswith(reverse("accounts:login"))
        client.post(reverse("accounts:login"), {"login": creator.phone, "password": TEST_PASSWORD})
        assert client.get(ADMIN_LIST).status_code == 200


class TestPlayerTutorial:
    def test_it_asks_for_login(self, client: Client) -> None:
        assert client.get(reverse("tutorial")).status_code == 302
