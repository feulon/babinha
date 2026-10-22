import random
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player
from community import services
from core.clock import FixedClock
from finance.models import Charge, ChargeKind, ChargeStatus
from fines.models import Fine, FineReason
from matchday import services as matchday
from matchday.domain.score import Side
from tests.conftest import SP, Sunday, end_game

pytestmark = pytest.mark.django_db

KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)
AFTER_GAME = datetime(2026, 10, 18, 11, 30, tzinfo=SP)
MONDAY_LIST_OPENS = datetime(2026, 10, 19, 8, 0, tzinfo=SP)
PLAYERS = reverse("community:players")
HTMX = {"HTTP_HX_REQUEST": "true"}


def profile_url(player: Player, **query: str) -> str:
    url = reverse("community:player", kwargs={"player_id": player.pk})
    return f"{url}?{'&'.join(f'{k}={v}' for k, v in query.items())}" if query else url


def login(player: Player) -> Client:
    client = Client()
    client.force_login(player)
    return client


def text(response) -> str:
    return response.content.decode()


@pytest.fixture
def ready(fill_sunday: Callable[..., Sunday], board_member: Player) -> Sunday:
    sunday = fill_sunday()
    matchday.draw(sunday.game_day.pk, board_member, random.Random(3))
    matchday.confirm(sunday.game_day.pk, board_member)
    return sunday


@pytest.fixture
def scorer(ready: Sunday, board_member: Player, clock: FixedClock) -> Player:
    """Scored both goals of Time A 2 x 0 Time B, the only match of the Sunday."""
    clock.set(KICKOFF)
    match = matchday.add_match(ready.game_day.pk, board_member, home_team=1, away_team=2)
    player = match.lineup.spots.filter(team=match.home_team).select_related("player")[0].player
    for _ in range(2):
        matchday.add_goal(match.pk, board_member, side=Side.HOME, scorer=player.pk)
    clock.set(AFTER_GAME)
    end_game(ready.game_day)
    ready.game_day.refresh_from_db()
    return player


@pytest.fixture
def sunday(ready: Sunday, scorer: Player) -> Sunday:
    return ready


@pytest.fixture
def other(sunday: Sunday, scorer: Player) -> Player:
    return next(p for p in sunday.line if p.pk != scorer.pk)


class TestTabs:
    def test_login_required(self, client: Client, scorer: Player) -> None:
        for url in (PLAYERS, profile_url(scorer), profile_url(scorer, aba="domingos")):
            response = client.get(url)
            assert response.status_code == 302
            assert reverse("accounts:login") in response["Location"]

    def test_summary_is_the_default_tab(self, scorer: Player, other: Player) -> None:
        page = text(login(other).get(profile_url(scorer)))
        assert scorer.name in page
        assert 'aria-current="page">Resumo' in page
        assert "Todas as temporadas" in page
        assert "Últimos domingos" in page
        assert "100%" in page  # went to the one Sunday there was

    @pytest.mark.parametrize("tab", ["resumo", "domingos", "bolas", "origem"])
    def test_every_tab_renders(self, scorer: Player, other: Player, tab: str) -> None:
        response = login(other).get(profile_url(scorer, aba=tab))
        assert response.status_code == 200
        assert 'id="perfil-abas"' in text(response)

    def test_sundays_tab_has_the_score_of_each_match(self, scorer: Player, other: Player) -> None:
        page = text(login(other).get(profile_url(scorer, aba="domingos")))
        assert "18/10/2026" in page
        assert "2×0" in page  # noqa: RUF001
        assert "res--V" in page

    @pytest.mark.parametrize("query", [{"aba": "financas"}, {"temporada": "9"}, {"temporada": "x"}])
    def test_unknown_options_are_not_found(self, scorer: Player, other: Player, query) -> None:
        assert login(other).get(profile_url(scorer, **query)).status_code == 404

    def test_season_filter(self, scorer: Player, other: Player, board_member: Player) -> None:
        services.end_season(board_member)
        client = login(other)
        old = text(client.get(profile_url(scorer, temporada="1")))
        assert ">Temporada 1</h2>" in old
        new = text(client.get(profile_url(scorer, temporada="2")))
        assert ">Temporada 2</h2>" in new
        assert "Ainda não jogou nenhum domingo nesta temporada" in new

    def test_history_restore_gets_the_full_page(self, scorer: Player, other: Player) -> None:
        response = login(other).get(
            profile_url(scorer, aba="bolas"), **HTMX, HTTP_HX_HISTORY_RESTORE_REQUEST="true"
        )
        assert "<html" in text(response)

    def test_unknown_player(self, other: Player) -> None:
        assert login(other).get(reverse("community:player", args=[99999])).status_code == 404


class TestGoldBalls:
    def vote(self, sunday: Sunday, winner: Player, second: Player, third: Player) -> None:
        for voter in sunday.line[10:13]:
            services.cast_vote(
                sunday.game_day.pk, voter, first=winner.pk, second=second.pk, third=third.pk
            )

    def test_only_after_the_vote_closes(
        self, sunday: Sunday, clock: FixedClock, scorer: Player, other: Player
    ) -> None:
        winner, second, third = sunday.line[3:6]
        self.vote(sunday, winner, second, third)
        viewer = login(other)
        before = text(viewer.get(profile_url(winner, aba="bolas")))
        assert "Ainda não ganhou nenhuma Bola de Ouro" in before
        assert "0 vezes em 2º" in text(viewer.get(profile_url(second, aba="bolas")))
        assert "0 vezes em 1º lugar" in before

        clock.set(MONDAY_LIST_OPENS)
        after = text(viewer.get(profile_url(winner, aba="bolas")))
        assert "18/10/2026" in after
        assert "Ainda não ganhou" not in after
        assert "1 vez em 1º lugar" in after
        assert "0 vezes em 2º lugar" in after
        assert "0 vezes em 3º lugar" in after
        assert "1 vez em 2º lugar" in text(viewer.get(profile_url(second, aba="bolas")))
        assert "1 vez em 3º lugar" in text(viewer.get(profile_url(third, aba="bolas")))
        sundays = text(viewer.get(profile_url(winner, aba="domingos")))
        assert "Bola de Ouro" in sundays


class TestOrigin:
    def test_who_referred_and_who_was_brought(
        self, scorer: Player, other: Player, make_player: Callable[..., Player]
    ) -> None:
        friend = make_player("Amigo do Gol", referred_by=scorer, member_since=date(2025, 3, 2))
        client = login(other)
        page = text(client.get(profile_url(scorer, aba="origem")))
        assert profile_url(scorer.referred_by, aba="origem") in page
        assert scorer.referred_by.name in page
        assert friend.name in page
        assert "02/03/2025" in page
        friend_page = text(client.get(profile_url(friend, aba="origem")))
        assert "02/03/2025" in friend_page
        assert profile_url(scorer, aba="origem") in friend_page

    def test_the_founder(self, president: Player, other: Player) -> None:
        assert "Fundador" in text(login(other).get(profile_url(president, aba="origem")))


class TestPrivacy:
    """Public to anyone logged in: totals, history, Bolas, podiums, presence and origin.
    The player and the diretoria: charges, fines and Pix. Only the diretoria: e-mail, pote."""

    @pytest.fixture
    def money(self, scorer: Player) -> Charge:
        Fine.objects.create(
            player=scorer,
            game_day=scorer.lineup_spots.get().lineup.game_day,
            reason=FineReason.NO_SHOW,
            ordinal=1,
            amount=Decimal("10.00"),
        )
        return Charge.objects.create(
            player=scorer,
            kind=ChargeKind.MONTHLY,
            amount=Decimal("123.00"),
            reference_date=date(2026, 10, 1),
            due_date=date(2026, 10, 10),
            status=ChargeStatus.PENDING,
        )

    def test_another_player_sees_only_the_public_parts(
        self, scorer: Player, other: Player, money: Charge
    ) -> None:
        client = login(other)
        for tab in ("resumo", "domingos", "bolas", "origem"):
            page = text(client.get(profile_url(scorer, aba=tab)))
            assert 'data-private="' not in page
            assert "R$" not in page
            assert scorer.email not in page
            assert "Mensalidade" not in page
            assert "Multas pendentes" not in page
            assert "Editar" not in page
            assert "pote" not in page.lower().replace("pótes", "")

    def test_the_player_sees_their_own_money_but_not_the_board_data(
        self, scorer: Player, money: Charge
    ) -> None:
        page = text(login(scorer).get(profile_url(scorer)))
        assert 'data-private="payments"' in page
        assert "Só você e a diretoria veem" in page
        assert "1 cobrança, R$ 123,00 a pagar" in page
        assert "Mensalidade" not in page
        assert "Multas pendentes" not in page
        assert reverse("finance:payments") not in page
        assert 'data-private="board"' not in page
        assert "Editar" not in page

    def test_the_board_sees_money_email_and_pote_with_edit(
        self, scorer: Player, board_member: Player, money: Charge
    ) -> None:
        page = text(login(board_member).get(profile_url(scorer)))
        assert "Só a diretoria vê" in page
        assert "R$ 123,00 a pagar" in page
        assert scorer.email in page
        assert f"pote {scorer.pote}" in page
        assert "Pagamentos e Pix" not in page.split('<nav class="tabbar"')[0]
        edit = reverse("board:player_edit", kwargs={"player_id": scorer.pk})
        assert edit not in page  # opened from Turma: no management
        from_board = text(
            login(board_member).get(profile_url(scorer), {"next": reverse("board:players")})
        )
        assert edit in from_board

    def test_the_board_looking_at_themselves_sees_both(self, board_member: Player) -> None:
        page = text(login(board_member).get(profile_url(board_member)))
        assert 'data-private="payments"' in page
        assert 'data-private="board"' in page

    def test_the_amount_to_pay_is_private_too(
        self, scorer: Player, other: Player, money: Charge
    ) -> None:
        assert "a pagar" in text(login(scorer).get(profile_url(scorer)))
        assert "a pagar" not in text(login(other).get(profile_url(scorer)))

    def test_nobody_else_sees_a_player_who_is_up_to_date_as_having_debts(
        self, scorer: Player
    ) -> None:
        page = text(login(scorer).get(profile_url(scorer)))
        assert "Em dia" in page


class TestVoteStaysAnonymous:
    def vote(self, sunday: Sunday, voter: Player, picks: list[Player]) -> None:
        services.cast_vote(
            sunday.game_day.pk,
            voter,
            first=picks[0].pk,
            second=picks[1].pk,
            third=picks[2].pk,
        )

    def test_the_profile_never_shows_who_the_player_picked(
        self, sunday: Sunday, board_member: Player
    ) -> None:
        voter = sunday.line[10]
        first, second, third = sunday.line[11:14]
        client = login(voter)
        self.vote(sunday, voter, [first, second, third])
        page = text(client.get(profile_url(voter)))
        for picked in (first, second, third):
            assert picked.name not in page

    def test_neither_the_board_nor_other_players_see_a_ballot(
        self, sunday: Sunday, board_member: Player, clock: FixedClock
    ) -> None:
        voter = sunday.line[10]
        self.vote(sunday, voter, list(sunday.line[11:14]))
        clock.set(MONDAY_LIST_OPENS)
        for viewer in (board_member, sunday.line[11]):
            page = text(login(viewer).get(profile_url(voter)))
            assert "você votou" not in page
            assert "votou" not in page.replace("quem votou em quem é anônimo", "")


class TestDirectory:
    def test_lists_active_players_by_name(
        self, sunday: Sunday, make_player, board_member: Player
    ) -> None:
        make_player("Aposentado", is_active=False)
        page = text(login(board_member).get(PLAYERS))
        assert reverse("community:player", args=[sunday.line[0].pk]) in page
        assert "Aposentado" not in page
        assert page.index("Goleiro 1") < page.index("Linha 01")

    def test_search_and_kind_filter(self, sunday: Sunday, board_member: Player) -> None:
        client = login(board_member)
        found = text(client.get(PLAYERS, {"q": "linha 07"}))
        assert "Linha 07" in found
        assert "Linha 08" not in found
        keepers = text(client.get(PLAYERS, {"tipo": "goleiro"}))
        assert "Goleiro 1" in keepers
        assert "Linha 01" not in keepers
        assert "Nenhum jogador encontrado" in text(client.get(PLAYERS, {"q": "zzzz"}))

    def test_unknown_filter(self, other: Player) -> None:
        assert login(other).get(PLAYERS, {"tipo": "vip"}).status_code == 404

    def test_totals_in_the_list(self, sunday: Sunday, scorer: Player, other: Player) -> None:
        assert "1 dom." in text(login(other).get(PLAYERS, {"q": scorer.name}))
