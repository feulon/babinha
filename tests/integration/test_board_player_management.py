"""Editing and creating players as the diretoria: name, active flag, same-name warning, a new
player outside any Sunday, and what an edit does (not) change on the Sunday's list."""

import random
from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player, PlayerType, Position
from games import services as games
from games.domain.types import EntryStatus
from games.models import ListEntry
from matchday import services
from tests.conftest import Sunday

pytestmark = pytest.mark.django_db

PLAYERS = reverse("board:players")


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


def edit_url(player: Player) -> str:
    return reverse("board:player_edit", kwargs={"player_id": player.pk})


def form_data(player: Player, **changes: object) -> dict[str, object]:
    data = {
        "name": player.name,
        "nickname": player.nickname,
        "phone": player.phone,
        "email": player.email or "",
        "player_type": player.player_type,
        "mensalista": "on" if player.is_mensalista else "",
        "position": player.position,
        "pote": player.pote,
        "is_active": "on" if player.is_active else "",
        "is_diretoria": "on" if player.is_diretoria else "",
        "referred_by": player.referred_by_id or "",
        "member_since": "",
    }
    data.update(changes)
    return {key: value for key, value in data.items() if value != ""}


class TestRename:
    def test_the_name_can_be_corrected(self, board_client: Client, make_player) -> None:
        player = make_player("Rafael")
        response = board_client.post(edit_url(player), form_data(player, name="  Rafa   Silva "))
        assert response.status_code == 302
        player.refresh_from_db()
        assert player.name == "Rafa Silva"

    def test_an_empty_name_is_refused(self, board_client: Client, make_player) -> None:
        player = make_player("Rafael")
        response = board_client.post(edit_url(player), form_data(player, name="   "))
        assert response.status_code == 200
        player.refresh_from_db()
        assert player.name == "Rafael"


class TestSameName:
    def test_taking_a_nickname_somebody_has_asks_to_confirm(
        self, board_client: Client, make_player
    ) -> None:
        make_player("Careca")
        other = make_player("Carlos")

        refused = board_client.post(edit_url(other), form_data(other, nickname="careca"))

        assert refused.status_code == 200
        html = refused.content.decode()
        assert "Já existe um jogador com o apelido careca" in html
        assert "Salvar assim mesmo" in html
        other.refresh_from_db()
        assert other.nickname == "Carlos"

        accepted = board_client.post(
            edit_url(other), form_data(other, nickname="careca", same_name_ok="on")
        )
        assert accepted.status_code == 302
        other.refresh_from_db()
        assert other.nickname == "careca"

    def test_a_player_already_sharing_a_nickname_is_not_nagged_when_it_stays(
        self, board_client: Client, make_player
    ) -> None:
        make_player("Careca")
        twin = make_player("Careca")
        response = board_client.post(edit_url(twin), form_data(twin, pote=2))
        assert response.status_code == 302

    def test_an_inactive_player_is_out_of_the_list_picker_and_the_players_screen(
        self, board_client: Client, make_player, clock
    ) -> None:
        gone = make_player("Saiu Do Baba")
        board_client.post(edit_url(gone), form_data(gone, is_active=""), follow=True)
        gone.refresh_from_db()
        assert not gone.is_active

        day = reverse("board:game_day", kwargs={"day": "2026-10-18"})
        assert "Saiu Do Baba" not in board_client.get(day).content.decode()
        assert "Saiu Do Baba" not in board_client.get(PLAYERS).content.decode()
        assert "Saiu Do Baba" in board_client.get(PLAYERS, {"inativos": "1"}).content.decode()

    def test_an_inactive_player_is_not_offered_as_the_inviter(
        self, board_client: Client, make_player
    ) -> None:
        gone = make_player("Saiu Do Baba")
        Player.objects.filter(pk=gone.pk).update(is_active=False)
        other = make_player("Carlos")
        html = board_client.get(edit_url(other)).content.decode()
        assert "Saiu Do Baba" not in html

    def test_the_diretoria_cannot_switch_itself_or_the_presidente_off(
        self, board_client: Client, board_member: Player, president: Player
    ) -> None:
        for target in (board_member, president):
            board_client.post(edit_url(target), form_data(target, is_active=""))
            target.refresh_from_db()
            assert target.is_active

    def test_an_inactive_player_cannot_sign_in(self, make_player) -> None:
        player = make_player("Saiu Do Baba")
        Player.objects.filter(pk=player.pk).update(is_active=False)
        client = Client()
        response = client.post(
            reverse("accounts:login"), {"login": player.phone, "password": "ab12"}
        )
        assert response.status_code == 200


class TestNewPlayer:
    def data(self, **changes: object) -> dict[str, object]:
        return {
            "name": "Zé Novo",
            "nickname": "Zé Novo",
            "phone": "(11) 98765-4321",
            "email": "ze@example.com",
            "player_type": PlayerType.AVULSO,
            "position": Position.LINHA,
            "pote": 2,
            **changes,
        }

    def test_creates_the_account_without_touching_any_list(
        self, board_client: Client, board_member: Player, clock
    ) -> None:
        response = board_client.post(reverse("board:player_new"), self.data())
        assert response.status_code == 302
        player = Player.objects.get(name="Zé Novo")
        assert player.referred_by == board_member
        assert player.pote == 2
        assert not ListEntry.objects.filter(player=player).exists()

        shown = board_client.get(response["Location"])
        assert "data-new-login" in shown.content.decode()
        assert "data-new-login" not in board_client.get(PLAYERS).content.decode()

    def test_warns_when_the_nickname_is_taken(self, board_client: Client, make_player) -> None:
        make_player("Zé Novo")
        response = board_client.post(reverse("board:player_new"), self.data())
        assert response.status_code == 200
        assert "Já existe um jogador com o apelido Zé Novo" in response.content.decode()
        assert Player.objects.filter(name="Zé Novo").count() == 1
        again = board_client.post(reverse("board:player_new"), self.data(same_name_ok="on"))
        assert again.status_code == 302
        assert Player.objects.filter(name="Zé Novo").count() == 2

    def test_the_new_player_signs_in_with_the_phone_and_the_shown_password(
        self, board_client: Client
    ) -> None:
        response = board_client.post(
            reverse("board:player_new"), self.data(phone="(21) 98888-7777"), follow=True
        )
        password = response.context["new_login"]["password"]
        login = Client().post(
            reverse("accounts:login"), {"login": "21988887777", "password": password}
        )
        assert login.status_code == 302

    @pytest.mark.parametrize("field", ["phone", "email"])
    def test_phone_and_email_are_required(self, board_client: Client, field: str) -> None:
        count = Player.objects.count()
        response = board_client.post(reverse("board:player_new"), self.data(**{field: ""}))
        assert response.status_code == 200
        assert "Este campo é obrigatório." in response.content.decode()
        assert Player.objects.count() == count

    def test_a_duplicate_phone_or_email_creates_nothing(
        self, board_client: Client, make_player
    ) -> None:
        taken = make_player("Maria")
        count = Player.objects.count()
        by_phone = board_client.post(reverse("board:player_new"), self.data(phone=taken.phone))
        assert "Já existe uma conta com este celular." in by_phone.content.decode()
        by_email = board_client.post(reverse("board:player_new"), self.data(email=taken.email))
        assert "Já existe uma conta com este e-mail." in by_email.content.decode()
        assert Player.objects.count() == count

    def test_a_plain_player_cannot(self, make_player) -> None:
        client = Client()
        client.force_login(make_player("Comum"))
        response = client.post(reverse("board:player_new"), self.data())
        assert response.status_code != 200
        assert not Player.objects.filter(name="Zé Novo").exists()


class TestSundayWarnings:
    @pytest.fixture
    def sunday(self, fill_sunday: Callable[..., Sunday]) -> Sunday:
        return fill_sunday()

    def save(self, client: Client, player: Player, **changes: object):
        return client.post(edit_url(player), form_data(player, **changes), follow=True)

    def warnings(self, response) -> list[str]:
        return [m.message for m in response.context["messages"] if m.level_tag == "warning"]

    def test_changing_the_position_of_someone_on_the_list_says_it_does_not_move_them(
        self, sunday: Sunday, board_client: Client
    ) -> None:
        player = sunday.line[0]
        response = self.save(board_client, player, position=Position.GOLEIRO)
        (warning,) = self.warnings(response)
        assert player.name in warning
        assert "como linha" in warning
        assert "retire e coloque de novo" in warning

    def test_changing_the_type_of_someone_on_the_list_is_flagged(
        self, sunday: Sunday, board_client: Client
    ) -> None:
        response = self.save(board_client, sunday.line[0], mensalista="on")
        (warning,) = self.warnings(response)
        assert "cobrança" in warning

    def test_changing_the_pote_of_someone_in_the_teams_is_flagged(
        self, sunday: Sunday, board_client: Client, board_member: Player
    ) -> None:
        services.draw(sunday.game_day.pk, board_member, random.Random(3))
        player = sunday.line[0]
        response = self.save(board_client, player, pote=5 if player.pote != 5 else 1)
        (warning,) = self.warnings(response)
        assert "pote novo só vale em um novo sorteio" in warning

    def test_changing_the_pote_before_the_draw_needs_no_warning(
        self, sunday: Sunday, board_client: Client
    ) -> None:
        player = sunday.line[0]
        response = self.save(board_client, player, pote=5 if player.pote != 5 else 1)
        assert self.warnings(response) == []

    def test_someone_off_the_list_gets_no_warning(
        self, sunday: Sunday, board_client: Client, make_player
    ) -> None:
        outsider = make_player("De fora")
        response = self.save(board_client, outsider, position=Position.GOLEIRO)
        assert self.warnings(response) == []

    def test_deactivating_someone_still_on_the_list_says_so(
        self, sunday: Sunday, board_client: Client
    ) -> None:
        response = self.save(board_client, sunday.line[0], is_active="")
        (warning,) = self.warnings(response)
        assert "continua na lista" in warning

    def test_waiting_list_counts_as_on_the_list(
        self,
        fill_sunday: Callable[..., Sunday],
        board_client: Client,
        make_player,
    ) -> None:
        sunday = fill_sunday()
        extra = make_player("Na espera")
        games.join_list(sunday.game_day.pk, extra)
        assert ListEntry.objects.get(player=extra).status == EntryStatus.ESPERA.value
        response = self.save(board_client, extra, position=Position.GOLEIRO)
        (warning,) = self.warnings(response)
        assert "na espera" in warning
