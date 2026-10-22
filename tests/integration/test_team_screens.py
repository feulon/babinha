"""The team assembly screen: draw, adjust and confirm, as the diretoria uses them."""

import random
import re
from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from matchday import services, team_rules
from matchday.models import Lineup, LineupSpot, RookieRelease, TogetherGroup
from tests.conftest import Sunday
from tests.integration.test_team_rules import grouping, invite, played_before

pytestmark = pytest.mark.django_db

DAY = "2026-10-18"
HTMX = {"HTTP_HX_REQUEST": "true"}


def url(name: str, **kwargs: object) -> str:
    return reverse(f"board:{name}", kwargs={"day": DAY, **kwargs})


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday]) -> Sunday:
    return fill_sunday()


def text(response) -> str:
    return response.content.decode()


def draw(sunday: Sunday, board: Player, seed: int = 5) -> Lineup:
    return services.draw(sunday.game_day.pk, board, random.Random(seed))


def in_one_row(html: str, *names: str) -> bool:
    """Whether `names` are all in the same tappable row of the teams (a group moves whole)."""
    rows = [row for row in re.findall(r"<li>.*?</li>", html, re.S) if "spot__row" in row]
    return any(all(name in row for name in names) for row in rows)


def team_number(player: Player) -> int | None:
    spot = LineupSpot.objects.select_related("team").get(player=player)
    return spot.team.number if spot.team else None


class TestAccess:
    @pytest.mark.parametrize(
        ("name", "kwargs"),
        [
            ("rookie_rule", {}),
            ("create_group", {}),
            ("delete_group", {"group_id": 1}),
            ("release_rookie", {"player_id": 1}),
            ("restore_rookie", {"player_id": 1}),
            ("draw_teams", {}),
            ("resolve_conflict", {}),
            ("assign_teams", {}),
        ],
    )
    def test_players_cannot_use_the_rules(self, sunday: Sunday, name: str, kwargs: dict) -> None:
        client = Client()
        client.force_login(sunday.line[0])
        assert client.post(url(name, **kwargs)).status_code == 403
        assert client.get(url("teams")).status_code == 403

    def test_rules_are_post_only(self, sunday: Sunday, board_client: Client) -> None:
        assert board_client.get(url("rookie_rule")).status_code == 405
        assert board_client.get(url("create_group")).status_code == 405


class TestRules:
    def test_the_switch(self, sunday: Sunday, board_client: Client) -> None:
        invite(sunday.line[7], sunday.line[0])
        response = board_client.post(url("rookie_rule"), {"enabled": "off"}, follow=True)
        html = text(response)
        assert "Regra dos estreantes desligada." in html
        assert 'aria-checked="false"' in html
        assert "Desligado: todos podem jogar em qualquer time." in html
        response = board_client.post(url("rookie_rule"), {"enabled": "on"}, follow=True)
        assert "Estreantes ficam com quem convidou." in text(response)
        assert len(grouping(sunday).groups) == 1

    def test_release_and_hold_again(self, sunday: Sunday, board_client: Client) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        released = board_client.post(url("release_rookie", player_id=bruno.pk), follow=True)
        assert f"{bruno.name} vai poder jogar em outro time, só neste domingo." in text(released)
        assert "Prender de novo" in text(released)
        assert RookieRelease.objects.filter(player=bruno).exists()
        held = board_client.post(url("restore_rookie", player_id=bruno.pk), follow=True)
        assert "O estreante voltou a ficar com quem convidou." in text(held)
        assert not RookieRelease.objects.exists()

    def test_make_a_group(self, sunday: Sunday, board_client: Client) -> None:
        picked = [str(p.pk) for p in sunday.line[:3]]
        response = board_client.post(
            url("create_group"), {"players": picked, "name": "Mesmo carro"}, follow=True
        )
        html = text(response)
        assert "Grupo salvo." in html
        assert "Mesmo carro" in html
        assert TogetherGroup.objects.get().members.count() == 3
        # Whoever is already in a group is no longer on offer for a new one.
        form_html = html[html.index('id="novo-grupo"') :]
        assert sunday.line[0].name not in form_html
        assert sunday.line[3].name in form_html

    def test_a_group_needs_two_players(self, sunday: Sunday, board_client: Client) -> None:
        for data in ({"players": [str(sunday.line[0].pk)]}, {}):
            response = board_client.post(url("create_group"), data)
            assert response.status_code == 200
            assert "Escolha de 2 a 5 jogadores para o grupo." in text(response)
        assert not TogetherGroup.objects.exists()

    def test_a_group_of_six_is_refused(self, sunday: Sunday, board_client: Client) -> None:
        picked = [str(p.pk) for p in sunday.line[:6]]
        response = board_client.post(url("create_group"), {"players": picked})
        assert "Escolha de 2 a 5 jogadores para o grupo." in text(response)

    def test_an_unknown_player_is_refused(self, sunday: Sunday, board_client: Client) -> None:
        response = board_client.post(
            url("create_group"), {"players": [str(sunday.line[0].pk), "99999"]}
        )
        assert response.status_code == 200
        assert not TogetherGroup.objects.exists()

    def test_a_player_already_in_a_group_is_refused(
        self, sunday: Sunday, board_client: Client
    ) -> None:
        board_client.post(url("create_group"), {"players": [str(p.pk) for p in sunday.line[:2]]})
        response = board_client.post(
            url("create_group"), {"players": [str(sunday.line[1].pk), str(sunday.line[2].pk)]}
        )
        assert response.status_code == 200
        assert "que ainda não estão num grupo" in text(response)
        assert TogetherGroup.objects.count() == 1

    def test_undo_a_group(self, sunday: Sunday, board_client: Client, board_member: Player) -> None:
        group = team_rules.create_group(
            sunday.game_day.pk, board_member, [p.pk for p in sunday.line[:2]]
        )
        response = board_client.post(url("delete_group", group_id=group.pk), follow=True)
        assert "Grupo desfeito." in text(response)
        assert not TogetherGroup.objects.exists()
        response = board_client.post(url("delete_group", group_id=group.pk), follow=True)
        assert "Grupo não encontrado." in text(response)


class TestDraw:
    @pytest.fixture
    def conflict(self, sunday: Sunday, board_member: Player) -> None:
        """A group of five and a first-timer bound to one of them: six who must be together."""
        team_rules.create_group(sunday.game_day.pk, board_member, [p.pk for p in sunday.line[:5]])
        invite(sunday.line[10], sunday.line[0])

    def test_the_draw_keeps_the_rules(
        self, sunday: Sunday, board_client: Client, board_member: Player
    ) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        trio = sunday.line[2:5]
        team_rules.create_group(sunday.game_day.pk, board_member, [p.pk for p in trio], "Carro")
        html = text(board_client.post(url("draw_teams"), follow=True))
        assert team_number(rafa) == team_number(bruno)
        assert len({team_number(p) for p in trio}) == 1
        assert in_one_row(html, *(p.name for p in trio))

    def test_a_failed_draw_leads_back_to_the_same_explanation(
        self, sunday: Sunday, board_client: Client, conflict: None
    ) -> None:
        response = board_client.post(url("draw_teams"))
        assert response.status_code == 302
        assert 'data-alert="rules-conflict"' in text(board_client.get(response["Location"]))
        assert not Lineup.objects.exists()

    def test_taking_the_suggested_way_out(
        self, sunday: Sunday, board_client: Client, conflict: None
    ) -> None:
        rookie = sunday.line[10]
        response = board_client.post(
            url("resolve_conflict"), {"exit": f"liberar:{rookie.pk}"}, follow=True
        )
        assert (
            f"{rookie.name} vai poder jogar em outro time, só neste domingo. Times sorteados."
            in text(response)
        )
        assert Lineup.objects.exists()
        assert len({team_number(p) for p in sunday.line[:5]}) == 1

    def test_taking_another_way_out(
        self, sunday: Sunday, board_client: Client, conflict: None
    ) -> None:
        leaving = sunday.line[4]
        response = board_client.post(
            url("resolve_conflict"), {"exit": f"remover:{leaving.pk}"}, follow=True
        )
        assert f"{leaving.name} saiu do grupo. Times sorteados." in text(response)
        assert len({team_number(p) for p in (*sunday.line[:4], sunday.line[10])}) == 1

    def test_accepting_a_bigger_team(
        self, sunday: Sunday, board_client: Client, conflict: None
    ) -> None:
        response = board_client.post(url("resolve_conflict"), {"exit": "aceitar"}, follow=True)
        html = text(response)
        sizes = sorted(
            LineupSpot.objects.filter(team=t).count() for t in Lineup.objects.get().teams.all()
        )
        assert sizes == [4, 5, 5, 6]
        assert 'data-alert="imbalance"' in html
        assert "Times desequilibrados" in html

    def test_going_back_is_not_a_way_out_any_more(
        self, sunday: Sunday, board_client: Client, conflict: None
    ) -> None:
        response = board_client.post(url("resolve_conflict"), {"exit": "voltar"}, follow=True)
        assert "Essa saída não vale mais." in text(response)
        assert not Lineup.objects.exists()

    def test_a_stale_choice_is_explained(
        self, sunday: Sunday, board_client: Client, conflict: None
    ) -> None:
        response = board_client.post(
            url("resolve_conflict"), {"exit": "remover:99999"}, follow=True
        )
        assert "Essa saída não vale mais." in text(response)
        assert not Lineup.objects.exists()

    def test_cannot_draw_again_once_confirmed(
        self, sunday: Sunday, board_client: Client, board_member: Player
    ) -> None:
        draw(sunday, board_member)
        services.confirm(sunday.game_day.pk, board_member)
        response = board_client.post(url("draw_teams"), follow=True)
        assert "Os times já foram confirmados." in text(response)


class TestHtmx:
    def test_an_action_swaps_only_the_block_and_pushes_the_page(
        self, sunday: Sunday, board_client: Client
    ) -> None:
        response = board_client.post(url("draw_teams"), **HTMX)
        html = text(response)
        assert response.status_code == 200
        assert "<html" not in html
        assert 'id="times"' in html
        assert 'id="messages"' in html
        assert "Times sorteados." in html
        assert response["HX-Push-Url"] == url("teams")

    def test_picking_a_player_is_a_plain_get_that_pushes_nothing(
        self, sunday: Sunday, board_member: Player, board_client: Client
    ) -> None:
        draw(sunday, board_member)
        response = board_client.get(url("teams"), {"unidade": f"u{sunday.line[0].pk}"}, **HTMX)
        assert "<html" not in text(response)
        assert "HX-Push-Url" not in response

    def test_a_form_error_keeps_the_page(self, sunday: Sunday, board_client: Client) -> None:
        response = board_client.post(url("create_group"), {"players": []}, **HTMX)
        assert response.status_code == 200
        assert "Escolha de 2 a 5 jogadores para o grupo." in text(response)


class TestTeams:
    def test_a_move(self, sunday: Sunday, board_member: Player, board_client: Client) -> None:
        draw(sunday, board_member)
        mover = sunday.line[0]
        target = 1 if team_number(mover) != 1 else 2
        response = board_client.post(
            url("assign_teams"),
            {"modo": "mover", "unidade": f"u{mover.pk}", "destino": str(target)},
            follow=True,
        )
        html = text(response)
        assert f"{mover.name} foi para o Time" in html
        assert team_number(mover) == target
        assert "data-pick=" not in html  # the pick is over
        assert "Times desequilibrados" in html  # six and four, said briefly

    def test_a_swap(self, sunday: Sunday, board_member: Player, board_client: Client) -> None:
        draw(sunday, board_member)
        first = sunday.line[0]
        second = next(p for p in sunday.line if team_number(p) != team_number(first))
        a, b = team_number(first), team_number(second)
        response = board_client.post(
            url("assign_teams"),
            {"modo": "trocar", "unidade": f"u{first.pk}", "destino": f"u{second.pk}"},
            follow=True,
        )
        html = text(response)
        assert f"{first.name} e {second.name} trocaram de lugar." in html
        assert (team_number(first), team_number(second)) == (b, a)
        assert "Times desequilibrados" not in html

    def test_a_move_without_a_team_keeps_the_pick(
        self, sunday: Sunday, board_member: Player, board_client: Client
    ) -> None:
        draw(sunday, board_member)
        mover = sunday.line[0]
        response = board_client.post(
            url("assign_teams"), {"modo": "mover", "unidade": f"u{mover.pk}"}
        )
        html = text(response)
        assert "Escolha para qual time mover." in html
        assert f'data-pick="u{mover.pk}"' in html

    def test_a_swap_needs_someone_to_swap_with(
        self, sunday: Sunday, board_member: Player, board_client: Client
    ) -> None:
        draw(sunday, board_member)
        first = sunday.line[0]
        response = board_client.post(
            url("assign_teams"), {"modo": "trocar", "unidade": f"u{first.pk}"}
        )
        assert "Escolha com quem trocar." in text(response)
        same_team = next(p for p in sunday.line[1:] if team_number(p) == team_number(first))
        response = board_client.post(
            url("assign_teams"),
            {"modo": "trocar", "unidade": f"u{first.pk}", "destino": f"u{same_team.pk}"},
        )
        assert "Escolha com quem trocar." in text(response)

    def test_moving_to_the_goal(
        self, fill_sunday: Callable[..., Sunday], board_member: Player, board_client: Client
    ) -> None:
        # 21 line players: the goal needs exactly one. Give the drawn one a team, send another.
        sunday = fill_sunday(line=21, goalkeepers=1)
        draw(sunday, board_member)
        keeper = next(p for p in sunday.line if team_number(p) is None)
        mover = next(p for p in sunday.line if team_number(p) == 1)
        board_client.post(
            url("assign_teams"),
            {"modo": "mover", "unidade": f"u{keeper.pk}", "destino": "1"},
        )
        html = text(board_client.get(url("teams"), {"unidade": f"u{mover.pk}"}))
        assert "Mover para o gol" in html
        response = board_client.post(
            url("assign_teams"),
            {"modo": "mover", "unidade": f"u{mover.pk}", "destino": "gol"},
            follow=True,
        )
        assert team_number(mover) is None
        assert f"{mover.name} foi para o gol." in text(response)
        goal = text(response).split('data-team="No gol"')[1].split("</section>")[0]
        assert mover.name in goal

    def test_nobody_else_can_be_sent_to_a_full_goal(
        self, fill_sunday: Callable[..., Sunday], board_member: Player, board_client: Client
    ) -> None:
        sunday = fill_sunday(line=21, goalkeepers=1)
        draw(sunday, board_member)
        mover = next(p for p in sunday.line if team_number(p) == 1)
        html = text(board_client.get(url("teams"), {"unidade": f"u{mover.pk}"}))
        assert "Mover para o gol" not in html
        response = board_client.post(
            url("assign_teams"),
            {"modo": "mover", "unidade": f"u{mover.pk}", "destino": "gol"},
            follow=True,
        )
        assert "Com 21 de linha só 1 vai ao gol" in text(response)
        assert team_number(mover) == 1

    def test_twenty_line_players_cannot_be_sent_to_goal(
        self, sunday: Sunday, board_member: Player, board_client: Client
    ) -> None:
        draw(sunday, board_member)
        mover = sunday.line[0]
        before = team_number(mover)
        response = board_client.post(
            url("assign_teams"),
            {"modo": "mover", "unidade": f"u{mover.pk}", "destino": "gol"},
            follow=True,
        )
        assert "Com 20 de linha ninguém precisa ir ao gol" in text(response)
        assert team_number(mover) == before

    def test_the_goal_counts_in_the_balance_of_quantities(
        self, fill_sunday: Callable[..., Sunday], board_member: Player, board_client: Client
    ) -> None:
        # The drawn goal player goes to a team: six in it, nobody in goal, and the screen says so.
        sunday = fill_sunday(line=21, goalkeepers=1)
        draw(sunday, board_member)
        keeper = next(p for p in sunday.line if team_number(p) is None)
        board_client.post(
            url("assign_teams"), {"modo": "mover", "unidade": f"u{keeper.pk}", "destino": "1"}
        )
        html = text(board_client.get(url("teams")))
        assert "Com 21 de linha, 1 de linha vai ao gol, mas ninguém está no gol." in html

    def test_a_group_moves_whole_and_is_one_line(
        self, sunday: Sunday, board_member: Player, board_client: Client
    ) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        draw(sunday, board_member)
        html = text(board_client.get(url("teams"))).split('data-card="sortear"')[0]
        assert in_one_row(html, rafa.name, bruno.name)
        assert html.count(bruno.name) == 1
        target = 1 if team_number(rafa) != 1 else 2
        response = board_client.post(
            url("assign_teams"),
            {"modo": "mover", "unidade": f"u{rafa.pk}", "destino": str(target)},
            follow=True,
        )
        assert f"{rafa.name} + {bruno.name} foram para o Time" in text(response)
        assert team_number(rafa) == team_number(bruno) == target

    def test_a_bound_player_is_not_a_unit_on_their_own(
        self, sunday: Sunday, board_member: Player, board_client: Client
    ) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        played_before(*(p for p in sunday.line if p != bruno))
        draw(sunday, board_member)
        html = text(board_client.get(url("teams")))
        assert in_one_row(html, rafa.name, bruno.name)
        assert f"u{bruno.pk}" not in html
        response = board_client.post(
            url("assign_teams"), {"modo": "mover", "unidade": f"u{bruno.pk}", "destino": "1"}
        )
        assert "Escolha quem mover." in text(response)

    def test_a_bound_first_timer_can_be_let_go_and_moved_alone(
        self, sunday: Sunday, board_member: Player, board_client: Client
    ) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        played_before(*(p for p in sunday.line if p != bruno))
        team_rules.create_group(
            sunday.game_day.pk, board_member, [p.pk for p in sunday.line[1:3]], "Carro"
        )
        draw(sunday, board_member)
        html = text(board_client.get(url("teams"), {"unidade": f"u{rafa.pk}"}))
        assert "Estreantes jogam com quem convidou." in html
        assert f"Soltar {bruno.name}" in html
        # a group by hand has nothing to let go of
        carro = text(board_client.get(url("teams"), {"unidade": f"u{sunday.line[1].pk}"}))
        assert "Soltar" not in carro
        board_client.post(url("release_rookie", player_id=bruno.pk), follow=True)
        target = 1 if team_number(bruno) != 1 else 2
        moved = board_client.post(
            url("assign_teams"),
            {"modo": "mover", "unidade": f"u{bruno.pk}", "destino": str(target)},
            follow=True,
        )
        assert team_number(bruno) == target
        assert team_number(rafa) != target  # he is on his own now
        assert f"{bruno.name} foi para o" in text(moved)

    def test_a_latecomer_is_picked_and_the_team_of_who_invited_them_is_suggested(
        self, sunday: Sunday, board_member: Player, board_client: Client, make_player
    ) -> None:
        rafa = sunday.line[0]
        draw(sunday, board_member)
        from games import services as games

        games.leave_list(sunday.game_day.pk, sunday.line[15], board_actor=board_member)
        late = make_player("Atrasado", referred_by=rafa)
        games.join_list(sunday.game_day.pk, late)
        html = text(board_client.get(url("teams")))
        assert "Entraram depois do sorteio" in html
        assert f'data-pick="u{rafa.pk}"' in html  # nobody had to be chosen
        assert html.count('btn--primary">Mover para cá') == 1
        # The pair is one unit: moving it brings the latecomer to the team and places them.
        origin = team_number(rafa)
        target = 1 if origin != 1 else 2
        board_client.post(
            url("assign_teams"),
            {"modo": "mover", "unidade": f"u{rafa.pk}", "destino": str(target)},
        )
        assert team_number(rafa) == team_number(late) == target

    def test_a_group_made_after_the_draw_must_be_brought_together_before_confirming(
        self, sunday: Sunday, board_member: Player, board_client: Client
    ) -> None:
        draw(sunday, board_member)
        first = sunday.line[0]
        second = next(p for p in sunday.line if team_number(p) != team_number(first))
        team_rules.create_group(sunday.game_day.pk, board_member, [first.pk, second.pk])
        html = text(board_client.get(url("teams")))
        assert 'data-alert="split"' in html
        assert "Precisam jogar juntos" in html
        assert f"{first.name} + {second.name}" in html
        assert f'data-pick="u{first.pk}"' in html  # it is who the screen asks to move
        response = board_client.post(url("confirm_teams"), follow=True)
        assert "precisam ficar no mesmo time" in text(response)

        target = team_number(first)
        response = board_client.post(
            url("assign_teams"),
            {"modo": "mover", "unidade": f"u{first.pk}", "destino": str(target)},
            follow=True,
        )
        assert 'data-alert="split"' not in text(response)
        assert team_number(first) == team_number(second) == target
        assert "Times confirmados" in text(board_client.post(url("confirm_teams"), follow=True))

    def test_confirming_uneven_teams_is_only_a_warning(
        self, sunday: Sunday, board_member: Player, board_client: Client
    ) -> None:
        draw(sunday, board_member)
        mover = sunday.line[0]
        target = 1 if team_number(mover) != 1 else 2
        board_client.post(
            url("assign_teams"),
            {"modo": "mover", "unidade": f"u{mover.pk}", "destino": str(target)},
        )
        html = text(board_client.get(url("teams")))
        assert "Times desequilibrados" in html
        assert "Confirmar times mesmo assim" in html
        assert 'hx-confirm="Os times estão desequilibrados' not in html
        response = board_client.post(url("confirm_teams"), follow=True)
        assert "Times confirmados" in text(response)
        assert Lineup.objects.get().is_confirmed

    def test_confirming_is_blocked_by_a_group_that_does_not_fit(
        self, sunday: Sunday, board_member: Player, board_client: Client
    ) -> None:
        rafa, bruno = sunday.line[0], sunday.line[7]
        invite(bruno, rafa)
        team_rules.create_group(
            sunday.game_day.pk, board_member, [rafa.pk, *(p.pk for p in sunday.line[1:5])]
        )
        team_rules.resolve_conflict(sunday.game_day.pk, board_member, "aceitar", random.Random(3))
        # Someone changes the rules afterwards: the six together no longer have anywhere to be.
        team_rules.set_rookie_rule(sunday.game_day.pk, board_member, True)
        response = board_client.post(url("confirm_teams"), follow=True)
        assert "tem 6 jogadores e cada time tem 5 vagas" in text(response)
        assert not Lineup.objects.get().is_confirmed
