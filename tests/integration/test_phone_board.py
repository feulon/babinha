"""The phone on the diretoria's screens, who may see it, and the password reset by the board."""

import random
from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from core.clock import FixedClock
from tests.conftest import TEST_PASSWORD, Sunday

pytestmark = pytest.mark.django_db

LOGIN = reverse("accounts:login")
PLAYERS = reverse("board:players")


def edit_url(player: Player) -> str:
    return reverse("board:player_edit", kwargs={"player_id": player.pk})


def reset_url(player: Player) -> str:
    return reverse("board:reset_player_password", kwargs={"player_id": player.pk})


def signed_in(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


def page(response) -> str:
    return response.content.decode()


def edit_data(player: Player, **overrides: object) -> dict:
    data = {
        "name": player.name,
        "is_active": "on",
        "phone": player.phone or "",
        "email": player.email or "",
        "player_type": player.player_type,
        "position": player.position,
        "pote": player.pote,
        "referred_by": player.referred_by_id or "",
        "member_since": "",
    }
    return {**data, **overrides}


@pytest.fixture
def maria(make_player: Callable[..., Player], president: Player) -> Player:
    """No e-mail, so no recovery link: only the board can give her a new password."""
    return make_player("Maria", email=None, phone="21977776666", referred_by=president)


@pytest.fixture
def board_client(board_member: Player) -> Client:
    return signed_in(board_member)


class TestBoardEditsThePhone:
    def test_the_edit_screen_shows_and_saves_phone_and_email(
        self, board_client: Client, maria: Player
    ) -> None:
        html = page(board_client.get(edit_url(maria)))
        assert 'value="(21) 97777-6666"' in html
        assert "(21) 97777-6666" in html
        board_client.post(
            edit_url(maria), edit_data(maria, phone="(21) 96666-5555", email="Maria@Example.com")
        )
        maria.refresh_from_db()
        assert (maria.phone, maria.email) == ("21966665555", "maria@example.com")

    def test_the_email_is_optional_there_too(self, president: Player) -> None:
        signed_in(president).post(edit_url(president), edit_data(president, email=""))
        president.refresh_from_db()
        assert president.email is None

    def test_the_phone_cannot_be_taken_from_someone_else(
        self, board_client: Client, maria: Player, president: Player
    ) -> None:
        response = board_client.post(edit_url(maria), edit_data(maria, phone=president.phone))
        assert "Já existe uma conta com este celular." in page(response)
        maria.refresh_from_db()
        assert maria.phone == "21977776666"

    def test_the_email_cannot_be_taken_from_someone_else(
        self, board_client: Client, maria: Player, president: Player
    ) -> None:
        response = board_client.post(
            edit_url(maria), edit_data(maria, email=president.email.upper())
        )
        assert "Já existe uma conta com este e-mail." in page(response)

    def test_a_phone_cannot_be_cleared_once_there_is_one(
        self, board_client: Client, maria: Player
    ) -> None:
        response = board_client.post(edit_url(maria), edit_data(maria, phone=""))
        assert "Este campo é obrigatório." in page(response)

    def test_a_player_still_without_phone_can_be_edited_and_is_flagged(
        self, board_client: Client, make_player: Callable[..., Player]
    ) -> None:
        legacy = make_player("Antigo")
        Player.objects.filter(pk=legacy.pk).update(phone=None)
        legacy.refresh_from_db()
        assert "Sem celular" in page(board_client.get(PLAYERS))
        response = board_client.post(edit_url(legacy), edit_data(legacy, pote=2))
        assert response.status_code == 302
        legacy.refresh_from_db()
        assert (legacy.pote, legacy.phone) == (2, None)

    def test_only_the_board_edits(self, maria: Player, make_player: Callable[..., Player]) -> None:
        other = make_player("Outro")
        assert signed_in(other).get(edit_url(maria)).status_code == 403
        response = signed_in(other).post(edit_url(maria), edit_data(maria, phone="21966665555"))
        assert response.status_code == 403


class TestBoardResetsThePassword:
    def test_a_player_without_email_gets_a_new_password_shown_once(
        self, board_client: Client, maria: Player
    ) -> None:
        response = board_client.post(reset_url(maria), follow=True)
        html = page(response)
        assert "data-new-password" in html
        assert "(21) 97777-6666" in html
        assert "https://wa.me/5521977776666" in html
        assert "no-store" in response["Cache-Control"]
        password = response.context["new_password"]["password"]
        assert len(password) == 4
        maria.refresh_from_db()
        assert maria.check_password(password)
        assert not maria.check_password(TEST_PASSWORD)
        assert "data-new-password" not in page(board_client.get(edit_url(maria)))

    def test_the_player_then_signs_in_with_the_phone_and_the_new_password(
        self, board_client: Client, maria: Player
    ) -> None:
        response = board_client.post(reset_url(maria), follow=True)
        password = response.context["new_password"]["password"]
        assert (
            Client().post(LOGIN, {"login": "21977776666", "password": TEST_PASSWORD}).status_code
            == 200
        )
        assert (
            Client().post(LOGIN, {"login": "21977776666", "password": password}).status_code == 302
        )

    def test_the_old_sessions_of_the_player_end(self, board_client: Client, maria: Player) -> None:
        player_client = signed_in(maria)
        assert player_client.get(reverse("games:home")).status_code == 200
        board_client.post(reset_url(maria))
        assert player_client.get(reverse("games:home")).status_code == 302

    def test_the_filters_survive_the_reset(self, board_client: Client, maria: Player) -> None:
        response = board_client.post(f"{reset_url(maria)}?q=Mar")
        assert response["Location"] == f"{edit_url(maria)}?q=Mar"

    def test_it_only_shows_on_the_screen_of_that_player(
        self, board_client: Client, maria: Player, president: Player
    ) -> None:
        board_client.post(reset_url(maria))
        assert "data-new-password" not in page(board_client.get(edit_url(president)))

    def test_a_plain_player_cannot(self, maria: Player, make_player: Callable[..., Player]) -> None:
        other = make_player("Outro")
        assert signed_in(other).post(reset_url(maria)).status_code == 403
        maria.refresh_from_db()
        assert maria.check_password(TEST_PASSWORD)

    def test_it_is_a_post(self, board_client: Client, maria: Player) -> None:
        assert board_client.get(reset_url(maria)).status_code == 405

    def test_the_board_cannot_take_over_the_presidente(
        self, board_client: Client, president: Player
    ) -> None:
        assert board_client.post(reset_url(president)).status_code == 403
        assert "Definir nova senha" not in page(board_client.get(edit_url(president)))
        president.refresh_from_db()
        assert president.check_password(TEST_PASSWORD)

    def test_the_presidente_can_reset_the_board(
        self, president: Player, board_member: Player
    ) -> None:
        assert signed_in(president).post(reset_url(board_member)).status_code == 302
        board_member.refresh_from_db()
        assert not board_member.check_password(TEST_PASSWORD)

    def test_resetting_your_own_password_keeps_you_signed_in(self, board_member: Player) -> None:
        client = signed_in(board_member)
        client.post(reset_url(board_member))
        assert client.get(edit_url(board_member)).status_code == 200


class TestPrivacy:
    """The phone is visible to the player and the diretoria only: never in lists or public
    profiles. The e-mail follows the same rule."""

    SECRETS = ("21977776666", "(21) 97777-6666", "97777-6666", "maria@example.com")

    @pytest.fixture
    def maria(self, make_player: Callable[..., Player], president: Player) -> Player:
        return make_player(
            "Maria", phone="21977776666", email="maria@example.com", referred_by=president
        )

    @pytest.fixture
    def other(self, make_player: Callable[..., Player], president: Player) -> Player:
        return make_player("Outro", referred_by=president)

    def assert_hidden(self, html: str) -> None:
        for secret in self.SECRETS:
            assert secret not in html
        assert "wa.me" not in html

    def test_other_players_never_see_it(self, maria: Player, other: Player, clock) -> None:
        client = signed_in(other)
        profile = reverse("community:player", kwargs={"player_id": maria.pk})
        for tab in ("resumo", "domingos", "bolas", "origem"):
            self.assert_hidden(page(client.get(f"{profile}?aba={tab}")))
        for kind in ("todos", "mensalistas", "avulsos"):
            self.assert_hidden(page(client.get(f"{reverse('community:players')}?tipo={kind}")))
        self.assert_hidden(page(client.get(reverse("community:players"), {"q": "Maria"})))
        self.assert_hidden(page(client.get(reverse("games:home"))))
        self.assert_hidden(page(client.get(reverse("community:ranking"))))

    def test_the_game_day_roster_for_players_hides_it(
        self, maria: Player, other: Player, clock: FixedClock
    ) -> None:
        from games import services as games

        games.join_list(games.current_game_day().pk, maria)
        self.assert_hidden(page(signed_in(other).get(reverse("games:home"))))

    def test_the_player_sees_their_own(self, maria: Player) -> None:
        profile = reverse("community:player", kwargs={"player_id": maria.pk})
        html = page(signed_in(maria).get(profile))
        assert "(21) 97777-6666" in html
        assert "maria@example.com" in html
        assert "Só você e a diretoria veem" in html

    def test_the_player_without_email_sees_that_it_is_missing(self, maria: Player) -> None:
        Player.objects.filter(pk=maria.pk).update(email=None)
        profile = reverse("community:player", kwargs={"player_id": maria.pk})
        html = page(signed_in(maria).get(profile))
        assert "não informado" in html
        assert "None" not in html

    def test_the_board_sees_it_on_the_profile_with_a_whatsapp_link(
        self, maria: Player, board_member: Player
    ) -> None:
        profile = reverse("community:player", kwargs={"player_id": maria.pk})
        html = page(signed_in(board_member).get(profile))
        assert "(21) 97777-6666" in html
        assert "maria@example.com" in html
        assert "https://wa.me/5521977776666" in html
        assert "Só a diretoria vê" in html

    def test_the_board_sees_it_in_the_list(self, maria: Player, board_member: Player) -> None:
        html = page(signed_in(board_member).get(PLAYERS))
        assert "(21) 97777-6666" in html
        assert "maria@example.com" in html

    def test_the_board_roster_stays_free_of_it(
        self, maria: Player, board_member: Player, clock: FixedClock
    ) -> None:
        from games import services as games

        games.join_list(games.current_game_day().pk, maria)
        day = games.current_game_day().date.isoformat()
        html = page(signed_in(board_member).get(reverse("board:game_day", kwargs={"day": day})))
        assert "97777-6666" not in html

    def test_a_plain_player_cannot_open_the_board_screens(
        self, maria: Player, other: Player
    ) -> None:
        client = signed_in(other)
        assert client.get(PLAYERS).status_code == 403
        assert client.get(edit_url(maria)).status_code == 403


@pytest.mark.usefixtures("clock")
def test_team_assembly_screens_do_not_carry_phones(
    fill_sunday: Callable[..., Sunday], board_member: Player
) -> None:
    from matchday import services as matchday

    sunday = fill_sunday()
    matchday.draw(sunday.game_day.pk, board_member, random.Random(3))
    url = reverse("board:teams", kwargs={"day": sunday.game_day.date.isoformat()})
    response = signed_in(board_member).get(url)
    assert response.status_code == 200
    html = page(response)
    assert not any(player.phone in html for player in sunday.line)
