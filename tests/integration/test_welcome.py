"""The welcome: four screens for the player and four for the diretoria, shown once."""

from collections.abc import Callable
from datetime import time

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from tests.conftest import PIX_KEY, TEST_PASSWORD, TUESDAY

pytestmark = pytest.mark.django_db

LOGIN = reverse("accounts:login")
SIGNUP = reverse("accounts:signup")
HOME = reverse("games:home")
DASHBOARD = reverse("board:dashboard")
SKIP = reverse("accounts:welcome_skip")


def screen(step: int) -> str:
    return reverse("accounts:welcome", kwargs={"step": step})


def board_screen(step: int) -> str:
    return reverse("accounts:welcome_board", kwargs={"step": step})


def sign_in(client: Client, player: Player) -> str:
    response = client.post(LOGIN, {"login": player.phone, "password": TEST_PASSWORD})
    assert response.status_code == 302
    return response["Location"]


@pytest.fixture
def newcomer(make_player: Callable[..., Player]) -> Player:
    return make_player("Novato", welcome_seen_at=None, board_welcome_seen_at=None)


@pytest.fixture
def new_board_member(make_player: Callable[..., Player], president: Player) -> Player:
    return make_player(
        "Diretor novo",
        is_diretoria=True,
        referred_by=president,
        welcome_seen_at=None,
        board_welcome_seen_at=None,
    )


@pytest.fixture
def logged_in(newcomer: Player) -> Client:
    client = Client()
    client.force_login(newcomer)
    return client


class TestFirstLoginAsPlayer:
    def test_the_first_login_opens_the_first_screen(self, newcomer: Player) -> None:
        assert sign_in(Client(), newcomer) == screen(1)

    def test_a_next_does_not_skip_the_welcome(self, newcomer: Player) -> None:
        response = Client().post(
            LOGIN, {"login": newcomer.phone, "password": TEST_PASSWORD, "next": "/mais/"}
        )
        assert response["Location"] == screen(1)

    def test_it_does_not_come_back_at_the_next_login(self, newcomer: Player) -> None:
        client = Client()
        client.get(sign_in(client, newcomer))
        client.logout()
        assert sign_in(client, newcomer) == HOME

    def test_veterans_never_see_it(self, make_player: Callable[..., Player]) -> None:
        veteran = make_player("Veterano")
        assert sign_in(Client(), veteran) == HOME

    def test_each_screen_renders_full_screen_with_pular(self, logged_in: Client) -> None:
        headings = {
            1: "Sua semana no Babinha",
            2: "Entre na lista",
            3: "Pix em três passos",
            4: "Multas",
        }
        for step, heading in headings.items():
            response = logged_in.get(screen(step))
            html = response.content.decode()
            assert response.status_code == 200
            assert heading in html
            assert f"{step} de 4" in html
            assert 'class="tabbar"' not in html
            assert 'class="topbar"' not in html
            assert f'action="{SKIP}"' in html
            assert ">Pular</button>" in html

    def test_next_and_back_link_the_screens(self, logged_in: Client) -> None:
        first = logged_in.get(screen(1)).content.decode()
        assert f'href="{screen(2)}">Próxima' in first
        assert "Voltar" not in first
        second = logged_in.get(screen(2)).content.decode()
        assert f'href="{screen(3)}">Próxima' in second
        assert f'href="{screen(1)}">Voltar' in second

    def test_the_last_screen_ends_on_inicio(self, logged_in: Client) -> None:
        html = logged_in.get(screen(4)).content.decode()
        assert f'href="{HOME}">Ir para o Início' in html
        assert "Próxima" not in html
        assert f'href="{reverse("rules")}">Ler as regras completas' in html

    def test_there_are_only_four_screens(self, logged_in: Client) -> None:
        assert logged_in.get(screen(0)).status_code == 404
        assert logged_in.get(screen(5)).status_code == 404

    def test_a_signed_out_visitor_is_sent_to_log_in(self, client: Client) -> None:
        response = client.get(screen(1))
        assert response.status_code == 302
        assert response["Location"].startswith(LOGIN)

    def test_the_provisional_password_banner_waits_for_the_app(
        self, make_player: Callable[..., Player]
    ) -> None:
        player = make_player(welcome_seen_at=None, password_is_provisional=True)
        client = Client()
        client.force_login(player)
        assert "data-provisional-password" not in client.get(screen(1)).content.decode()


class TestRealValues:
    def test_the_week_follows_the_settings_and_the_calendar(
        self, logged_in: Client, settings, clock: FixedClock
    ) -> None:
        settings.BABA_LIST_OPENS_AT = time(9, 30)
        html = logged_in.get(screen(1)).content.decode()
        assert "Seg<br>09h30" in html
        assert "Sáb<br>23h59" in html
        assert "Dom<br>10h" in html
        assert "quarta, às 20h" in html
        assert "08h00" not in html

    def test_pix_and_the_amounts_come_from_the_club(
        self, logged_in: Client, club_values, clock: FixedClock
    ) -> None:
        html = logged_in.get(screen(3)).content.decode()
        assert "Copie a chave Pix, pague no seu banco e envie a foto ou o PDF" in html
        assert "R$ 30,00" in html
        assert "R$ 100,00" in html
        assert "vence dia 10" in html
        assert "12h30" in html
        assert PIX_KEY not in html

    def test_unset_values_say_a_definir(self, logged_in: Client) -> None:
        html = logged_in.get(screen(3)).content.decode()
        assert html.count("a definir") == 2

    def test_the_fines_come_from_the_club(
        self, logged_in: Client, club_values, clock: FixedClock
    ) -> None:
        club_values.fine_base = 12
        club_values.late_fine_15 = 7
        club_values.late_fine_30 = 14
        club_values.save()
        html = logged_in.get(screen(4)).content.decode()
        assert "R$ 12,00" in html
        assert "15 min custam R$ 7,00" in html
        assert "30 min custam R$ 14,00" in html
        assert "sábado 23h59" in html

    def test_signup_says_the_rules_were_accepted(self, newcomer: Player, logged_in: Client) -> None:
        assert "Você aceitou as regras no cadastro" not in logged_in.get(screen(4)).content.decode()
        Player.objects.filter(pk=newcomer.pk).update(rules_accepted_at=TUESDAY)
        assert "Você aceitou as regras no cadastro" in logged_in.get(screen(4)).content.decode()


class TestSignup:
    @staticmethod
    def data() -> dict[str, str]:
        return {
            "name": "Zico",
            "nickname": "Zico",
            "phone": "(11) 98765-4321",
            "email": "zico@example.com",
            "password": "gol1",
            "password_confirm": "gol1",
            "accept_rules": "on",
        }

    def test_signup_reaches_the_welcome_and_never_replays_the_rules(
        self, client: Client, president: Player
    ) -> None:
        response = client.post(SIGNUP, self.data())
        assert response["Location"] == screen(1)
        assert client.get(response["Location"]).status_code == 200
        player = Player.objects.get(name="Zico")
        assert player.welcome_seen_at is not None
        assert player.rules_accepted_at is not None
        client.logout()
        login = client.post(LOGIN, {"login": "11987654321", "password": "gol1"})
        assert login["Location"] == HOME


class TestSkip:
    def test_pular_counts_as_seen_and_goes_to_inicio(
        self, newcomer: Player, logged_in: Client
    ) -> None:
        response = logged_in.post(SKIP, {"set": "player"})
        assert response["Location"] == HOME
        newcomer.refresh_from_db()
        assert newcomer.welcome_seen_at is not None
        logged_in.logout()
        assert sign_in(logged_in, newcomer) == HOME

    def test_pular_only_accepts_a_post(self, logged_in: Client) -> None:
        assert logged_in.get(SKIP).status_code == 405

    def test_the_player_set_skip_leaves_the_board_set_for_the_painel(
        self, new_board_member: Player
    ) -> None:
        client = Client()
        client.force_login(new_board_member)
        client.post(SKIP, {"set": "player"})
        new_board_member.refresh_from_db()
        assert new_board_member.welcome_seen_at is not None
        assert new_board_member.board_welcome_seen_at is None

    def test_a_player_cannot_mark_the_board_set(self, newcomer: Player, logged_in: Client) -> None:
        response = logged_in.post(SKIP, {"set": "board"})
        assert response["Location"] == HOME
        newcomer.refresh_from_db()
        assert newcomer.board_welcome_seen_at is None


class TestFirstLoginAsBoard:
    def test_the_first_login_opens_the_player_screens(self, new_board_member: Player) -> None:
        assert sign_in(Client(), new_board_member) == screen(1)

    def test_the_last_player_screen_offers_the_board_set(self, new_board_member: Player) -> None:
        client = Client()
        client.force_login(new_board_member)
        html = client.get(screen(4)).content.decode()
        assert f'href="{board_screen(1)}">Ver a parte da diretoria' in html
        assert f'href="{HOME}">Ir para o Início' in html

    def test_a_plain_player_is_not_offered_the_board_set(self, logged_in: Client) -> None:
        assert "diretoria" not in logged_in.get(screen(4)).content.decode().lower()

    def test_the_board_screens_end_on_the_painel(self, new_board_member: Player) -> None:
        client = Client()
        client.force_login(new_board_member)
        headings = {
            1: "Você está na diretoria",
            2: "Antes do primeiro domingo",
            3: "No domingo",
            4: "Depois do jogo",
        }
        for step, heading in headings.items():
            html = client.get(board_screen(step)).content.decode()
            assert heading in html
            assert f"{step} de 4" in html
            assert 'class="tabbar"' not in html
            assert ">Pular</button>" in html
        assert f'href="{DASHBOARD}">Ir para o Painel' in html

    def test_the_board_screens_use_the_real_hours_and_the_shared_texts(
        self, new_board_member: Player, clock: FixedClock
    ) -> None:
        client = Client()
        client.force_login(new_board_member)
        sunday = client.get(board_screen(3)).content.decode()
        assert "Abre às 10h" in sunday
        for step in ("Montar os times", "Primeiros jogos", "Presença", "Finalizar e cobrar"):
            assert step in sunday
        assert "2 gols ou 7 minutos" in sunday
        assert "No seu perfil" in client.get(board_screen(2)).content.decode()

    def test_only_the_diretoria_opens_the_board_set(self, logged_in: Client) -> None:
        assert logged_in.get(board_screen(1)).status_code == 403

    def test_the_first_visit_to_the_painel_opens_the_board_welcome_once(
        self, new_board_member: Player, clock: FixedClock
    ) -> None:
        Player.objects.filter(pk=new_board_member.pk).update(welcome_seen_at=TUESDAY)
        client = Client()
        client.force_login(new_board_member)
        response = client.get(DASHBOARD)
        assert response["Location"] == board_screen(1)
        client.get(response["Location"])
        new_board_member.refresh_from_db()
        assert new_board_member.board_welcome_seen_at is not None
        assert client.get(DASHBOARD).status_code == 200

    def test_pular_on_the_board_set_goes_to_the_painel(
        self, new_board_member: Player, clock: FixedClock
    ) -> None:
        client = Client()
        client.force_login(new_board_member)
        response = client.post(SKIP, {"set": "board"})
        assert response["Location"] == DASHBOARD
        new_board_member.refresh_from_db()
        assert new_board_member.board_welcome_seen_at is not None
        assert client.get(DASHBOARD).status_code == 200

    def test_a_veteran_on_the_diretoria_is_not_interrupted(
        self, board_member: Player, clock: FixedClock
    ) -> None:
        client = Client()
        client.force_login(board_member)
        assert client.get(DASHBOARD).status_code == 200

    def test_a_player_promoted_later_sees_the_board_set_only(
        self, make_player: Callable[..., Player], clock: FixedClock
    ) -> None:
        veteran = make_player("Veterano")
        Player.objects.filter(pk=veteran.pk).update(is_diretoria=True, board_welcome_seen_at=None)
        client = Client()
        assert sign_in(client, veteran) == HOME
        assert client.get(DASHBOARD)["Location"] == board_screen(1)


class TestReopen:
    def test_ajuda_has_a_boas_vindas_item(self, logged_in: Client) -> None:
        html = logged_in.get(reverse("more")).content.decode()
        assert f'href="{screen(1)}"' in html
        assert "<strong>Boas-vindas</strong>" in html

    def test_reopening_changes_nothing(self, make_player: Callable[..., Player]) -> None:
        veteran = make_player("Veterano")
        before = (veteran.welcome_seen_at, veteran.board_welcome_seen_at)
        client = Client()
        client.force_login(veteran)
        for step in (1, 2, 3, 4):
            assert client.get(screen(step)).status_code == 200
        veteran.refresh_from_db()
        assert (veteran.welcome_seen_at, veteran.board_welcome_seen_at) == before

    def test_the_painel_has_a_button_for_the_board_set(
        self, board_member: Player, clock: FixedClock
    ) -> None:
        client = Client()
        client.force_login(board_member)
        html = client.get(DASHBOARD).content.decode()
        assert f'href="{board_screen(1)}"' in html
        assert "Boas-vindas da diretoria" in html
        before = board_member.board_welcome_seen_at
        for step in (1, 2, 3, 4):
            assert client.get(board_screen(step)).status_code == 200
        board_member.refresh_from_db()
        assert board_member.board_welcome_seen_at == before


@pytest.mark.django_db(transaction=True)
def test_the_migration_marks_everyone_who_already_exists():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [("accounts", "0016_player_nickname")]
    executor.migrate(previous)
    try:
        old = executor.loader.project_state(previous).apps.get_model("accounts", "Player")
        veteran = old.objects.create(name="A", nickname="A", phone="11911111111")
        board = old.objects.create(name="B", nickname="B", phone="11922222222", is_diretoria=True)
        head = old.objects.create(name="C", nickname="C", phone="11933333333", is_presidente=True)
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        player = executor.loader.project_state(latest).apps.get_model("accounts", "Player")
        veteran, board, head = (player.objects.get(pk=p.pk) for p in (veteran, board, head))
        assert veteran.welcome_seen_at is not None
        assert board.welcome_seen_at is not None
        assert head.welcome_seen_at is not None
        assert veteran.board_welcome_seen_at is None
        assert board.board_welcome_seen_at is not None
        assert head.board_welcome_seen_at is not None
    finally:
        MigrationExecutor(connection).migrate(latest)
