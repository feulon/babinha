"""The "Como montar o domingo" tutorial: a diretoria-only page under Ajuda."""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from games.domain import slots

pytestmark = pytest.mark.django_db

TUTORIAL = reverse("board:sunday_tutorial")
MORE = reverse("more")


def login(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


class TestAccess:
    def test_anonymous_is_sent_to_login(self, client: Client) -> None:
        assert client.get(TUTORIAL).status_code == 302

    def test_players_are_forbidden(self, make_player) -> None:
        assert login(make_player("Zico")).get(TUTORIAL).status_code == 403

    @pytest.mark.parametrize("who", ["board_member", "president"])
    def test_the_diretoria_reads_it(self, who: str, request: pytest.FixtureRequest) -> None:
        response = login(request.getfixturevalue(who)).get(TUTORIAL)
        assert response.status_code == 200
        assert "Como montar o domingo" in response.content.decode()

    def test_the_hidden_admin_reads_it(self, make_player) -> None:
        assert login(make_player("Criador", is_admin=True)).get(TUTORIAL).status_code == 200


class TestLinks:
    def test_ajuda_links_it_for_the_diretoria_only(self, board_member: Player, make_player) -> None:
        assert TUTORIAL in login(board_member).get(MORE).content.decode()
        assert TUTORIAL not in login(make_player("Zico")).get(MORE).content.decode()

    def test_the_dashboard_and_the_diretoria_tutorial_link_it(self, board_member: Player) -> None:
        client = login(board_member)
        assert TUTORIAL in client.get(reverse("board:dashboard")).content.decode()
        assert TUTORIAL in client.get(reverse("board:tutorial")).content.decode()


class TestContent:
    """The text states the numbers the list engine really uses."""

    def test_spot_numbers_match_the_domain(self, board_member: Player) -> None:
        html = login(board_member).get(TUTORIAL).content.decode()
        assert f"No máximo <strong>{slots.MAX_GOALKEEPERS} goleiros</strong>" in html
        assert f"<strong>{slots.FIXED_LINE_SLOTS} vagas fixas</strong>" in html
        assert f"<strong>{slots.FLEX_SLOTS} vagas flexíveis</strong>" in html
        assert [slots.line_capacity(n) for n in (0, 1, 2, 3, 4)] == [22, 21, 20, 20, 20]
        assert "Sem goleiro, a linha tem 22 vagas. Com 1 goleiro, 21. Com 2 ou mais, 20." in html
        assert "<strong>22 a 24 pessoas</strong>" in html

    def test_it_covers_the_whole_flow(self, board_member: Player) -> None:
        html = login(board_member).get(TUTORIAL).content.decode()
        for topic in (
            "fila de espera",
            "período livre",
            "Lugar",
            "Para a fila",
            "pote",
            "Sortear times",
            "Confirmar times",
            "Finalizar jogo e cobrar",
            "Bola de Ouro",
        ):
            assert topic in html
        assert "—" not in html


DAY = "2026-10-18"
GAME_DAY = reverse("board:game_day", kwargs={"day": DAY})
SEEN = reverse("board:sunday_tutorial_seen")


def unseen(player: Player) -> Player:
    Player.objects.filter(pk=player.pk).update(sunday_tutorial_seen_at=None)
    player.refresh_from_db()
    return player


class TestFirstTime:
    @pytest.mark.parametrize("who", ["board_member", "president"])
    def test_building_the_sunday_for_the_first_time_opens_the_tutorial(
        self, who: str, request: pytest.FixtureRequest, clock
    ) -> None:
        client = login(unseen(request.getfixturevalue(who)))
        response = client.get(GAME_DAY)
        assert response.status_code == 302
        assert response["Location"] == f"{TUTORIAL}?domingo={DAY}"
        html = client.get(response["Location"]).content.decode()
        assert "Entendi, montar o domingo" in html
        assert f'name="domingo" value="{DAY}"' in html

    def test_the_hidden_admin_gets_it_too(self, make_player, clock) -> None:
        admin_player = unseen(make_player("Criador", is_admin=True))
        assert login(admin_player).get(GAME_DAY).status_code == 302

    def test_dismissing_marks_it_seen_and_goes_on_to_the_sunday(
        self, board_member: Player, clock
    ) -> None:
        client = login(unseen(board_member))
        response = client.post(SEEN, {"domingo": DAY})
        assert response.status_code == 302
        assert response["Location"] == GAME_DAY
        board_member.refresh_from_db()
        assert board_member.sunday_tutorial_seen_at is not None

    def test_it_does_not_come_back_after_the_dismissal(self, board_member: Player, clock) -> None:
        client = login(unseen(board_member))
        client.post(SEEN, {"domingo": DAY})
        assert client.get(GAME_DAY).status_code == 200
        assert client.get(GAME_DAY).status_code == 200
        page = client.get(TUTORIAL).content.decode()
        assert "Entendi" not in page  # still readable from Ajuda, with no button to dismiss

    def test_reading_it_from_ajuda_does_not_dismiss_it(self, board_member: Player, clock) -> None:
        client = login(unseen(board_member))
        assert client.get(TUTORIAL).status_code == 200
        assert client.get(GAME_DAY).status_code == 302

    def test_the_dismissal_keeps_the_first_date(self, board_member: Player, clock) -> None:
        client = login(unseen(board_member))
        client.post(SEEN)
        first = Player.objects.get(pk=board_member.pk).sunday_tutorial_seen_at
        clock.advance(timedelta(days=1))
        client.post(SEEN)
        assert Player.objects.get(pk=board_member.pk).sunday_tutorial_seen_at == first

    def test_a_bad_day_goes_to_the_painel(self, board_member: Player, clock) -> None:
        response = login(unseen(board_member)).post(SEEN, {"domingo": "//evil.example"})
        assert response["Location"] == reverse("board:dashboard")

    def test_partial_updates_of_the_list_are_not_redirected(
        self, board_member: Player, clock
    ) -> None:
        client = login(unseen(board_member))
        assert client.get(GAME_DAY, HTTP_HX_REQUEST="true").status_code == 200

    def test_players_never_see_it_and_cannot_dismiss_it(self, make_player, clock) -> None:
        client = login(make_player("Zico"))
        assert client.get(GAME_DAY).status_code == 403
        assert client.get(TUTORIAL).status_code == 403
        assert client.post(SEEN).status_code == 403

    def test_the_flag_is_not_in_any_form(self) -> None:
        import accounts.forms
        import board.forms

        for module in (accounts.forms, board.forms):
            for form in vars(module).values():
                meta = getattr(form, "_meta", None)
                if meta is not None and meta.model is Player:
                    assert "sunday_tutorial_seen_at" not in meta.fields
