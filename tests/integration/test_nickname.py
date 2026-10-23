"""The nickname is what players see; the name is for the diretoria's money and sign-up screens."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from core.models import ClubSettings
from games import services
from games.models import GameDay
from tests.conftest import end_game

pytestmark = pytest.mark.django_db

SIGNUP = reverse("accounts:signup")


def signup_data(**changes: object) -> dict[str, object]:
    return {
        "name": "Carlos Eduardo",
        "nickname": "Carlão",
        "phone": "(11) 98765-4321",
        "email": "carlos@example.com",
        "password": "ab12",
        "password_confirm": "ab12",
        "accept_rules": "on",
        **changes,
    }


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def player_client(make_player, president: Player) -> Client:
    client = Client()
    client.force_login(make_player("Viewer Name", nickname="Viewer", referred_by=president))
    return client


@pytest.fixture
def careca(make_player, president: Player) -> Player:
    return make_player("Carlos Eduardo Lima", nickname="Careca", referred_by=president)


@pytest.mark.django_db(transaction=True)
def test_the_migration_fills_the_nickname_of_existing_players_with_their_name() -> None:
    before = [("accounts", "0015_tipos_primeiro_login")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    try:
        old = executor.loader.project_state(before).apps.get_model("accounts", "Player")
        old.objects.create(name="Mauro Silva", email="mauro@example.com", password="x")
        old.objects.create(name="N" * 60, email="longo@example.com", password="x")

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        nicknames = dict(Player.objects.values_list("email", "nickname"))
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        Player.objects.all().delete()

    assert nicknames == {"mauro@example.com": "Mauro Silva", "longo@example.com": "N" * 30}


class TestDisplayName:
    def test_it_is_the_nickname_and_falls_back_to_the_name(self, make_player) -> None:
        player = make_player("Carlos Eduardo", nickname="Carlão")
        assert player.display_name == "Carlão"
        assert str(player) == "Carlão"
        player.nickname = "  "
        assert player.display_name == "Carlos Eduardo"

    def test_the_board_label_tells_homonyms_apart(self, make_player) -> None:
        assert (
            make_player("Carlos Eduardo", nickname="Carlão").board_label
            == "Carlão (Carlos Eduardo)"
        )
        assert make_player("Rafa").board_label == "Rafa"


class TestSignup:
    def test_asks_for_the_name_then_the_nickname_without_help_text(
        self, client: Client, president: Player
    ) -> None:
        html = client.get(SIGNUP).content.decode()
        assert html.index('for="id_name">Nome<') < html.index('for="id_nickname">Apelido<')
        assert "nome ou apelido" not in html.lower()
        after_nickname = html.split('for="id_nickname"', 1)[1].split('for="', 1)[0]
        assert "field__help" not in after_nickname

    def test_creates_the_account_with_both(self, client: Client, president: Player) -> None:
        response = client.post(SIGNUP, signup_data())
        assert response.status_code == 302
        player = Player.objects.get(phone="11987654321")
        assert (player.name, player.nickname) == ("Carlos Eduardo", "Carlão")

    @pytest.mark.parametrize("nickname", ["", "   "])
    def test_needs_a_nickname(self, client: Client, president: Player, nickname: str) -> None:
        response = client.post(SIGNUP, signup_data(nickname=nickname))
        assert response.status_code == 200
        assert "id_nickname" in response.content.decode()
        assert response.context["form"].errors["nickname"]
        assert not Player.objects.filter(phone="11987654321").exists()

    def test_needs_a_name(self, client: Client, president: Player) -> None:
        response = client.post(SIGNUP, signup_data(name="  "))
        assert response.status_code == 200
        assert not Player.objects.filter(phone="11987654321").exists()

    def test_the_nickname_is_capped(self, client: Client, president: Player) -> None:
        response = client.post(SIGNUP, signup_data(nickname="x" * 31))
        assert response.status_code == 200
        assert not Player.objects.filter(phone="11987654321").exists()


class TestOwnProfile:
    def test_the_player_edits_name_and_nickname(self, make_player, president: Player) -> None:
        player = make_player("Carlos Eduardo", nickname="Carlos", referred_by=president)
        client = Client()
        client.force_login(player)
        url = reverse("accounts:profile")
        html = client.get(url).content.decode()
        assert html.index('for="id_name">Nome<') < html.index('for="id_nickname">Apelido<')
        response = client.post(url, {"name": "Carlos Eduardo Lima", "nickname": " Careca  "})
        assert response.status_code == 302
        player.refresh_from_db()
        assert (player.name, player.nickname) == ("Carlos Eduardo Lima", "Careca")

    def test_the_nickname_cannot_be_blank(self, make_player, president: Player) -> None:
        player = make_player("Carlos", referred_by=president)
        client = Client()
        client.force_login(player)
        response = client.post(reverse("accounts:profile"), {"name": "Carlos", "nickname": "  "})
        assert response.status_code == 200
        player.refresh_from_db()
        assert player.nickname == "Carlos"


class TestPlayersSeeTheNickname:
    def test_the_sunday_list_and_the_card_on_the_home(
        self, player_client: Client, careca: Player, clock: FixedClock
    ) -> None:
        services.join_list(services.current_game_day().pk, careca)
        html = player_client.get(reverse("games:home")).content.decode()
        assert "Careca" in html
        assert "Carlos Eduardo Lima" not in html

    def test_the_directory_and_the_profile(
        self, player_client: Client, careca: Player, clock: FixedClock
    ) -> None:
        for url in (
            reverse("community:players"),
            reverse("community:player", kwargs={"player_id": careca.pk}),
        ):
            html = player_client.get(url).content.decode()
            assert "Careca" in html, url
            assert "Carlos Eduardo Lima" not in html, url

    def test_the_directory_searches_the_nickname(
        self, player_client: Client, careca: Player
    ) -> None:
        found = player_client.get(reverse("community:players"), {"q": "Careca"})
        assert "Careca" in found.content.decode()
        assert (
            "Careca"
            not in player_client.get(reverse("community:players"), {"q": "Lima"}).content.decode()
        )

    def test_the_header_initial_is_the_nickname_initial(self, make_player, president) -> None:
        player = make_player("Zeca Silva", nickname="Bolinha", referred_by=president)
        client = Client()
        client.force_login(player)
        assert 'aria-hidden="true">B</span>' in client.get(reverse("games:home")).content.decode()


class TestBoardSundayAndPickers:
    def test_the_sunday_list_shows_the_nickname(
        self, board_client: Client, careca: Player, clock: FixedClock
    ) -> None:
        day = services.current_game_day()
        services.join_list(day.pk, careca)
        html = board_client.get(
            reverse("board:game_day", kwargs={"day": day.date.isoformat()})
        ).content.decode()
        assert "Careca" in html
        assert "Carlos Eduardo Lima" not in html.split("</select>", 1)[1]

    def test_the_picker_shows_nickname_and_name(
        self, board_client: Client, careca: Player, clock: FixedClock
    ) -> None:
        day = services.current_game_day()
        html = board_client.get(
            reverse("board:game_day", kwargs={"day": day.date.isoformat()})
        ).content.decode()
        assert "Careca (Carlos Eduardo Lima)" in html


class TestBoardMoneyScreensShowTheName:
    @pytest.fixture
    def owing(self, clock: FixedClock, club_values: ClubSettings, careca: Player) -> Player:
        day = services.current_game_day()
        services.join_list(day.pk, careca)
        end_game(GameDay.objects.get(pk=day.pk))
        return careca

    @pytest.mark.parametrize("url_name", ["board:pending", "board:cash", "board:group_charge"])
    def test_pending_cash_and_group_charge(
        self, board_client: Client, owing: Player, url_name: str
    ) -> None:
        html = board_client.get(reverse(url_name)).content.decode()
        assert "Carlos Eduardo Lima" in html

    def test_the_group_message_shows_the_nickname_not_the_name(
        self, board_client: Client, owing: Player
    ) -> None:
        html = board_client.get(reverse("board:group_charge")).content.decode()
        message = html.split("data-group-message>", 1)[1].split("</pre>", 1)[0]
        assert "Careca" in message
        assert "Carlos Eduardo Lima" not in message

    def test_the_player_list_shows_the_nickname_with_the_name_beside(
        self, board_client: Client, careca: Player
    ) -> None:
        html = board_client.get(reverse("board:players")).content.decode()
        assert "<strong>Careca</strong>" in html
        assert "Carlos Eduardo Lima" in html

    def test_the_player_list_is_searchable_by_both(
        self, board_client: Client, careca: Player
    ) -> None:
        for query in ("Careca", "eduardo"):
            html = board_client.get(reverse("board:players"), {"q": query}).content.decode()
            assert "<strong>Careca</strong>" in html


class TestBoardCreatesPlayers:
    def data(self, **changes: object) -> dict[str, object]:
        return {
            "name": "Zé Novo",
            "nickname": "Zezinho",
            "phone": "(11) 98765-4321",
            "email": "ze@example.com",
            "player_type": "avulso",
            "position": "linha",
            "pote": 2,
            **changes,
        }

    def test_the_nickname_is_required(self, board_client: Client) -> None:
        response = board_client.post(reverse("board:player_new"), self.data(nickname=" "))
        assert response.status_code == 200
        assert not Player.objects.filter(name="Zé Novo").exists()

    def test_it_saves_both(self, board_client: Client) -> None:
        board_client.post(reverse("board:player_new"), self.data())
        assert Player.objects.get(name="Zé Novo").nickname == "Zezinho"


class TestCriarPresidente:
    def run(self, monkeypatch, **options: str) -> Player:
        from django.core.management import call_command

        monkeypatch.setenv("BABA_BOOTSTRAP_PASSWORD", "ab12")
        call_command(
            "criar_presidente",
            telefone="(11) 98765-4321",
            email="p@example.com",
            nome="Paulo Roberto",
            **options,
        )
        return Player.objects.get(is_presidente=True)

    def test_accepts_the_nickname(self, monkeypatch) -> None:
        assert self.run(monkeypatch, apelido="Paulão").nickname == "Paulão"

    def test_without_it_the_nickname_is_the_name(self, monkeypatch) -> None:
        assert self.run(monkeypatch).nickname == "Paulo Roberto"


def test_the_demo_squad_has_a_common_name_apart_from_each_nickname() -> None:
    from demo.players import PLAYERS

    assert all(spec.name != spec.nickname for spec in PLAYERS)
    assert len({spec.name for spec in PLAYERS}) == len(PLAYERS)
