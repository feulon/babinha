from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Player, PlayerType, Position
from core.clock import FixedClock
from core.models import ClubSettings
from fines import services as fine_services
from fines.models import Fine, FineReason, FineStatus
from games import services
from games.models import GameDay, ListEntry
from tests.conftest import SP, TUESDAY, start_game

pytestmark = pytest.mark.django_db

HOME = reverse("games:home")
SUNDAY_MORNING = datetime(2026, 10, 18, 8, 0, tzinfo=SP)
AFTER_KICKOFF = datetime(2026, 10, 18, 10, 30, tzinfo=SP)


@pytest.fixture
def player(make_player: Callable[..., Player], president: Player) -> Player:
    return make_player("Ronaldinho", referred_by=president)


@pytest.fixture
def player_client(client: Client, player: Player) -> Client:
    client.force_login(player)
    return client


@pytest.fixture
def board_client(board_member: Player) -> Client:
    client = Client()
    client.force_login(board_member)
    return client


@pytest.fixture
def president_client(president: Player) -> Client:
    client = Client()
    client.force_login(president)
    return client


def state(response) -> str:
    html = response.content.decode()
    marker = 'data-state="'
    return html[html.index(marker) + len(marker) :].split('"', 1)[0]


class TestHome:
    def test_requires_login(self, client: Client) -> None:
        response = client.get(HOME)
        assert response.status_code == 302
        assert reverse("accounts:login") in response["Location"]

    def test_not_open_yet(self, player_client: Client, clock: FixedClock) -> None:
        clock.set(datetime(2026, 10, 12, 7, 0, tzinfo=SP))  # Monday before 08:00
        response = player_client.get(HOME)
        assert state(response) == "fechada"
        assert "segunda-feira, 12/10 às 08h00" in response.content.decode()

    def test_join_then_leave(self, player_client: Client, clock: FixedClock) -> None:
        assert state(player_client.get(HOME)) == "fora"
        response = player_client.post(reverse("games:join"), follow=True)
        assert state(response) == "lista"
        assert "Você está na lista deste domingo!" in response.content.decode()
        assert "Ronaldinho (você)" in response.content.decode()

        confirm_page = player_client.get(reverse("games:leave"))
        assert "Sem custo até sábado" in confirm_page.content.decode()
        response = player_client.post(reverse("games:leave"), follow=True)
        assert state(response) == "fora"

    def test_waitlist_shows_position(
        self, player_client: Client, player: Player, make_player, clock: FixedClock
    ) -> None:
        game_day = services.current_game_day()
        for _ in range(22):
            services.join_list(game_day.pk, make_player())
        services.join_list(game_day.pk, make_player())
        clock.advance(timedelta(minutes=1))
        response = player_client.post(reverse("games:join"), follow=True)
        assert state(response) == "espera"
        assert "Você é o 2º na espera" in response.content.decode()

    def test_promoted_and_late_decline_warns_about_the_fine(
        self, player_client: Client, player: Player, make_player, clock: FixedClock
    ) -> None:
        values = ClubSettings.load()
        values.fine_base = Decimal("15.00")
        values.save()
        game_day = services.current_game_day()
        others = [make_player() for _ in range(22)]
        for other in others:
            services.join_list(game_day.pk, other)
        services.join_list(game_day.pk, player)
        services.leave_list(game_day.pk, others[0])
        assert state(player_client.get(HOME)) == "promovido"

        clock.set(SUNDAY_MORNING)
        page = player_client.get(reverse("games:leave")).content.decode()
        assert "Vai gerar multa" in page
        assert "Multa de R$ 15,00 (1ª ocorrência)" in page
        response = player_client.post(reverse("games:leave"), follow=True)
        html = response.content.decode()
        assert "uma multa foi registrada" in html
        assert 'data-state="pagamento"' in html
        assert "R$ 15,00" in html
        payments = player_client.get(reverse("finance:payments")).content.decode()
        assert "Multa · domingo 18/10" in payments

    def test_a_mensalista_who_would_take_a_spot_still_sees_join(
        self, make_player, president: Player, clock: FixedClock
    ) -> None:
        game_day = services.current_game_day()
        for _ in range(22):
            services.join_list(game_day.pk, make_player())
        mensalista = make_player(player_type=PlayerType.MENSALISTA, referred_by=president)
        client = Client()
        client.force_login(mensalista)
        # In the free period a mensalista bumps the last avulso, so there is no waiting.
        html = client.get(HOME).content.decode()
        assert "Entrar na lista" in html
        assert "Entrar na fila de espera" not in html
        clock.set(SUNDAY_MORNING)
        assert "Entrar na fila de espera" in client.get(HOME).content.decode()

    def test_refresh_returns_only_the_block_and_keeps_the_messages(
        self, player_client: Client, clock: FixedClock
    ) -> None:
        page = player_client.get(HOME).content.decode()
        assert 'id="home" class="stack" data-live-refresh' in page
        assert "Atualizar" not in page
        player_client.post(reverse("games:join"))  # leaves a message for the next full page
        refresh = player_client.get(HOME, HTTP_HX_REQUEST="true")
        assert "HX-Request" in refresh["Vary"]
        html = refresh.content.decode()
        assert html.lstrip().startswith('<div id="home"')
        assert "<html" not in html
        assert "Você está na lista deste domingo!" not in html
        assert "Você está na lista deste domingo!" in player_client.get(HOME).content.decode()

    def test_late_leave_page_counts_the_occurrence(
        self, player_client: Client, player: Player, board_member: Player, clock: FixedClock
    ) -> None:
        values = ClubSettings.load()
        values.fine_base = Decimal("20.00")
        values.save()
        game_day = services.current_game_day()
        services.join_list(game_day.pk, player)
        past = GameDay.objects.create(date=datetime(2026, 10, 11, tzinfo=SP).date())
        fine_services.record_fine(player, past, FineReason.LATE_DROPOUT)
        clock.set(SUNDAY_MORNING)
        page = player_client.get(reverse("games:leave")).content.decode()
        assert "Multa de R$ 30,00 (2ª ocorrência)" in page

    def test_cancelled(
        self, player_client: Client, board_member: Player, clock: FixedClock
    ) -> None:
        services.cancel_game_day(services.current_game_day().pk, board_member, "Chuva forte")
        response = player_client.get(HOME)
        assert state(response) == "cancelado"
        assert "Chuva forte" in response.content.decode()

    def test_closed_after_kickoff(self, player_client: Client, clock: FixedClock) -> None:
        clock.set(AFTER_KICKOFF)
        start_game()
        assert state(player_client.get(HOME)) == "encerrada"

    def test_confirmed_player_cannot_leave_after_kickoff(
        self, player_client: Client, clock: FixedClock
    ) -> None:
        player_client.post(reverse("games:join"))
        clock.set(AFTER_KICKOFF)
        start_game()
        html = player_client.get(HOME).content.decode()
        assert 'data-state="lista"' in html
        assert reverse("games:leave") not in html
        assert "sem multa" not in html
        assert "O jogo deste domingo já começou." in html

        response = player_client.get(reverse("games:leave"))
        assert response.status_code == 302
        assert response["Location"] == HOME

    def test_waiting_player_cannot_leave_after_kickoff(
        self, player_client: Client, make_player, clock: FixedClock
    ) -> None:
        game_day = services.current_game_day()
        for _ in range(22):
            services.join_list(game_day.pk, make_player())
        player_client.post(reverse("games:join"))
        clock.set(AFTER_KICKOFF)
        start_game()
        html = player_client.get(HOME).content.decode()
        assert 'data-state="espera"' in html
        assert reverse("games:leave") not in html
        assert "você entra automaticamente" not in html
        assert "O jogo deste domingo já começou." in html
        assert player_client.get(reverse("games:leave"))["Location"] == HOME

    def test_pote_is_never_shown_to_players(
        self, player_client: Client, player: Player, clock: FixedClock
    ) -> None:
        player_client.post(reverse("games:join"))
        html = player_client.get(HOME).content.decode()
        assert "Pote" not in html
        assert "pote" not in html.lower().replace("potencial", "")


class TestBoardAccess:
    @pytest.mark.parametrize(
        "url",
        [
            reverse("board:dashboard"),
            reverse("board:players"),
            reverse("board:fines"),
            reverse("board:settings"),
            reverse("board:game_day", kwargs={"day": "2026-10-18"}),
        ],
    )
    def test_players_are_forbidden(self, player_client: Client, url: str, clock) -> None:
        assert player_client.get(url).status_code == 403

    def test_players_are_forbidden_and_have_no_diretoria_tab(
        self, player_client: Client, clock
    ) -> None:
        for url in (
            reverse("board:dashboard"),
            reverse("board:players"),
            reverse("community:season"),
        ):
            assert player_client.get(url).status_code == 403
        html = player_client.get(HOME).content.decode()
        assert 'data-tab="diretoria"' not in html


class TestDiretoriaTab:
    def test_losing_the_board_role_drops_the_tab(
        self, board_client: Client, board_member: Player, clock
    ) -> None:
        assert 'data-tab="diretoria"' in board_client.get(HOME).content.decode()
        Player.objects.filter(pk=board_member.pk).update(is_diretoria=False, is_presidente=False)
        assert 'data-tab="diretoria"' not in board_client.get(HOME).content.decode()

    def test_the_former_switch_address_needs_a_session(self, client: Client) -> None:
        response = client.post("/modo/", {"mode": "diretoria"})
        assert response.status_code == 302
        assert "entrar" in response.url


class TestBoardScreens:
    def test_edit_player_type_position_pote_and_diretoria(
        self, president_client: Client, player: Player, president: Player
    ) -> None:
        url = reverse("board:player_edit", kwargs={"player_id": player.pk})
        response = president_client.post(
            url,
            {
                "name": player.name,
                "nickname": player.nickname,
                "is_active": "on",
                "phone": player.phone,
                "player_type": PlayerType.AGREGADO,
                "mensalista": "on",
                "position": Position.GOLEIRO,
                "pote": "1",
                "is_diretoria": "on",
                "referred_by": president.pk,
            },
        )
        assert response.status_code == 302
        player.refresh_from_db()
        assert player.player_type == PlayerType.AGREGADO
        assert player.is_mensalista  # the checkbox joins them in this month's plan
        assert player.position == Position.GOLEIRO
        assert player.pote == 1
        assert player.is_board

    def test_pote_out_of_range_is_rejected(
        self, board_client: Client, player: Player, president: Player
    ) -> None:
        url = reverse("board:player_edit", kwargs={"player_id": player.pk})
        response = board_client.post(
            url,
            {
                "name": player.name,
                "nickname": player.nickname,
                "is_active": "on",
                "phone": player.phone,
                "player_type": "avulso",
                "position": "linha",
                "pote": "6",
                "referred_by": president.pk,
            },
        )
        assert response.status_code == 200
        player.refresh_from_db()
        assert player.pote == 3

    def test_only_the_president_changes_who_is_on_the_diretoria(
        self, board_client: Client, player: Player
    ) -> None:
        response = self.edit(board_client, player, is_diretoria="on", pote="1")
        assert response.status_code == 302
        player.refresh_from_db()
        assert player.pote == 1  # the rest of the form still saves
        assert not player.is_diretoria

    def test_the_diretoria_cannot_demote_a_board_member(
        self, board_client: Client, board_member: Player, make_player, president: Player
    ) -> None:
        other = make_player("Outro", is_diretoria=True, referred_by=president)
        self.edit(board_client, other)  # an unchecked box is a missing field
        other.refresh_from_db()
        assert other.is_diretoria

    def test_the_diretoria_cannot_edit_the_presidents_contact_or_type(
        self, board_client: Client, president: Player
    ) -> None:
        response = self.edit(
            board_client,
            president,
            phone="(21) 98888-7777",
            email="ladrao@example.com",
            player_type=PlayerType.AGREGADO,
            pote="1",
        )
        assert response.status_code == 302
        before = (president.phone, president.email, president.player_type)
        president.refresh_from_db()
        assert (president.phone, president.email, president.player_type) == before
        assert president.pote == 1

    def test_the_president_edits_their_own_contact(
        self, president_client: Client, president: Player
    ) -> None:
        response = self.edit(president_client, president, email="novo@example.com")
        assert response.status_code == 302
        president.refresh_from_db()
        assert president.email == "novo@example.com"

    def test_the_diretoria_still_edits_other_contacts(
        self, board_client: Client, player: Player
    ) -> None:
        self.edit(board_client, player, phone="(21) 98888-7777")
        player.refresh_from_db()
        assert player.phone == "21988887777"

    def test_president_keeps_board_powers(self, board_client: Client, president: Player) -> None:
        url = reverse("board:player_edit", kwargs={"player_id": president.pk})
        board_client.post(
            url,
            {
                "name": president.name,
                "nickname": president.nickname,
                "is_active": "on",
                "phone": president.phone,
                "player_type": "avulso",
                "position": "linha",
                "pote": "3",
            },
        )
        president.refresh_from_db()
        assert president.is_presidente
        assert president.is_board

    def edit(self, client: Client, player: Player, **fields: object):
        data = {
            "name": player.name,
            "nickname": player.nickname,
            "is_active": "on",
            "phone": player.phone,
            "player_type": "avulso",
            "position": "linha",
            "pote": "3",
        }
        data["referred_by"] = player.referred_by_id or ""
        data.update(fields)
        return client.post(reverse("board:player_edit", kwargs={"player_id": player.pk}), data)

    def test_board_corrects_who_referred_the_player(
        self, board_client: Client, player: Player, board_member: Player, make_player
    ) -> None:
        # Created by the diretoria, who is recorded as the referrer until corrected.
        player.referred_by = board_member
        player.save()
        friend = make_player("Quem trouxe")
        assert self.edit(board_client, player, referred_by=friend.pk).status_code == 302
        player.refresh_from_db()
        assert player.referred_by == friend

    def test_referrer_is_required_except_for_the_president(
        self, board_client: Client, player: Player, president: Player
    ) -> None:
        assert self.edit(board_client, player, referred_by="").status_code == 200
        player.refresh_from_db()
        assert player.referred_by == president
        assert self.edit(board_client, president, referred_by="").status_code == 302
        president.refresh_from_db()
        assert president.referred_by is None

    def test_player_without_referrer_can_be_edited_without_inventing_one(
        self, board_client: Client, player: Player
    ) -> None:
        player.referred_by = None
        player.save()
        response = self.edit(board_client, player, player_type="agregado", pote="1")
        assert response.status_code == 302
        player.refresh_from_db()
        assert player.player_type == PlayerType.AGREGADO
        assert player.pote == 1
        assert player.referred_by is None

    def test_referrer_cannot_close_a_cycle(
        self, board_client: Client, player: Player, make_player
    ) -> None:
        grandchild = make_player("Neto", referred_by=make_player("Filho", referred_by=player))
        response = self.edit(board_client, player, referred_by=grandchild.pk)
        assert response.status_code == 200
        assert "veio por indicação deste jogador" in response.content.decode()
        response = self.edit(board_client, player, referred_by=player.pk)
        assert response.status_code == 200

    def test_board_sets_member_since(self, board_client: Client, player: Player) -> None:
        assert self.edit(board_client, player, member_since="2025-03-02").status_code == 302
        player.refresh_from_db()
        assert str(player.member_since) == "2025-03-02"
        assert self.edit(board_client, player, member_since="").status_code == 302
        player.refresh_from_db()
        assert player.member_since is None
        assert player.joined_on == player.created_at.astimezone(SP).date()

    def test_member_since_cannot_be_in_the_future(
        self, board_client: Client, player: Player, clock: FixedClock
    ) -> None:
        assert self.edit(board_client, player, member_since="2099-01-01").status_code == 200
        player.refresh_from_db()
        assert player.member_since is None

    def test_a_player_cannot_edit_anyone(self, player_client: Client, player: Player) -> None:
        assert self.edit(player_client, player, member_since="2025-03-02").status_code == 403
        player.refresh_from_db()
        assert player.member_since is None

    def test_configure_values_in_brazilian_format(
        self, president_client: Client, president: Player
    ) -> None:
        response = president_client.post(
            reverse("board:settings"),
            {
                "monthly_fee": "120,00",
                "single_fee": "35,50",
                "fine_base": "20,00",
                "fine_factor": "1,50",
                "late_fine_15": "10,00",
                "late_fine_30": "25,00",
                "monthly_due_day": "5",
                "avulso_after_games": "5",
                "pix_key": " Presidente@Baba.com.br ",
            },
        )
        assert response.status_code == 302
        values = ClubSettings.load()
        assert values.single_fee == Decimal("35.50")
        assert values.fine_base == Decimal("20.00")
        assert values.monthly_due_day == 5
        assert values.pix_key == "presidente@baba.com.br"
        assert values.updated_by == president
        assert values.pix_key_changed_by == president
        assert values.pix_key_changed_at is not None
        assert values.is_configured

    def test_the_diretoria_changes_values_but_never_the_pix_key(
        self, board_client: Client, board_member: Player, club_values: ClubSettings
    ) -> None:
        original = club_values.pix_key
        response = board_client.post(
            reverse("board:settings"),
            {
                "monthly_fee": "120,00",
                "single_fee": "35,50",
                "fine_base": "20,00",
                "fine_factor": "1,50",
                "late_fine_15": "10,00",
                "late_fine_30": "25,00",
                "monthly_due_day": "5",
                "avulso_after_games": "5",
                "pix_key": "ladrao@example.com",
            },
        )
        assert response.status_code == 302
        values = ClubSettings.load()
        assert values.monthly_fee == Decimal("120.00")
        assert values.pix_key == original
        assert values.pix_key_changed_by is None

    def test_saving_other_values_does_not_credit_a_pix_key_change(
        self, president_client: Client, club_values: ClubSettings, president: Player
    ) -> None:
        response = president_client.post(
            reverse("board:settings"),
            {
                "monthly_fee": "130,00",
                "single_fee": "30,00",
                "fine_base": "10,00",
                "fine_factor": "1,50",
                "late_fine_15": "10,00",
                "late_fine_30": "25,00",
                "monthly_due_day": "10",
                "avulso_after_games": "5",
                "pix_key": club_values.pix_key,
            },
        )
        assert response.status_code == 302
        values = ClubSettings.load()
        assert values.monthly_fee == Decimal("130.00")
        assert values.pix_key_changed_by is None

    def test_invalid_pix_key_is_rejected(self, president_client: Client) -> None:
        # Each kind of key is checked in tests/unit/test_pix.py; here the form refuses one.
        data = dict.fromkeys(
            ["monthly_fee", "single_fee", "fine_base", "late_fine_15", "late_fine_30"], "1,00"
        )
        data.update(monthly_due_day="10", avulso_after_games="5", pix_key="chave qualquer")
        response = president_client.post(reverse("board:settings"), data)
        assert response.status_code == 200
        assert "Chave Pix inválida" in response.content.decode()
        assert ClubSettings.load().pix_key == ""

    def test_due_day_is_capped_at_28(self, board_client: Client) -> None:
        data = dict.fromkeys(
            ["monthly_fee", "single_fee", "fine_base", "late_fine_15", "late_fine_30"], "1,00"
        )
        response = board_client.post(
            reverse("board:settings"), {**data, "monthly_due_day": "31", "pix_key": ""}
        )
        assert response.status_code == 200
        assert ClubSettings.load().monthly_due_day == 10

    def test_negative_values_are_rejected(self, board_client: Client) -> None:
        data = dict.fromkeys(
            ["monthly_fee", "single_fee", "fine_base", "late_fine_15", "late_fine_30"], "1,00"
        )
        response = board_client.post(reverse("board:settings"), {**data, "fine_base": "-5"})
        assert response.status_code == 200
        assert ClubSettings.load().fine_base == Decimal("50.00")

    def test_add_and_remove_from_list(
        self, board_client: Client, player: Player, clock: FixedClock
    ) -> None:
        clock.set(TUESDAY - timedelta(days=2))  # Sunday afternoon, list not open yet
        day = services.current_game_day().date.isoformat()
        response = board_client.post(
            reverse("board:add_to_list", kwargs={"day": day}), {"player": player.pk}, follow=True
        )
        assert "Ronaldinho entrou na lista." in response.content.decode()
        response = board_client.post(
            reverse("board:remove_from_list", kwargs={"day": day, "player_id": player.pk}),
            follow=True,
        )
        assert "Ronaldinho foi retirado." in response.content.decode()
        assert ListEntry.objects.get(player=player).status == "saiu"

    def test_attendance_and_forgiveness(
        self, board_client: Client, player: Player, clock: FixedClock
    ) -> None:
        game_day = services.current_game_day()
        services.join_list(game_day.pk, player)
        day = game_day.date.isoformat()
        page = board_client.get(reverse("board:game_day", kwargs={"day": day})).content.decode()
        assert "Todos presentes" not in page

        clock.set(AFTER_KICKOFF)
        start_game()
        page = board_client.get(reverse("board:game_day", kwargs={"day": day})).content.decode()
        assert "Todos presentes" in page
        board_client.post(
            reverse("board:mark_attendance", kwargs={"day": day, "player_id": player.pk}),
            {"attendance": "faltou"},
        )
        fine = Fine.objects.get(player=player)

        forgive_url = reverse("board:forgive", kwargs={"fine_id": fine.pk})
        response = board_client.post(forgive_url, {"note": "Estava doente"}, follow=True)
        assert "Multa de Ronaldinho perdoada." in response.content.decode()
        fine.refresh_from_db()
        assert fine.status == FineStatus.FORGIVEN
        assert fine.resolution_note == "Estava doente"

    def test_cancel_and_reopen(self, board_client: Client, clock: FixedClock) -> None:
        day = services.current_game_day().date.isoformat()
        response = board_client.post(
            reverse("board:cancel", kwargs={"day": day}), {"reason": "Feriado"}, follow=True
        )
        assert "Domingo cancelado." in response.content.decode()
        assert GameDay.objects.get(date=day).is_cancelled
        board_client.post(reverse("board:reopen", kwargs={"day": day}))
        assert not GameDay.objects.get(date=day).is_cancelled

    def test_navigating_to_a_non_sunday_is_404(self, board_client: Client) -> None:
        url = reverse("board:game_day", kwargs={"day": "2026-10-17"})
        assert board_client.get(url).status_code == 404
        assert board_client.get(url.replace("2026-10-17", "lixo")).status_code == 404


class TestPlayerFines:
    def test_lists_own_fines_only(
        self, player_client: Client, player: Player, make_player, clock: FixedClock
    ) -> None:
        game_day = services.current_game_day()
        other = make_player("Outro")
        for someone in (player, other):
            services.join_list(game_day.pk, someone)
        clock.set(SUNDAY_MORNING)
        for someone in (player, other):
            services.leave_list(game_day.pk, someone)
        html = player_client.get(reverse("fines:mine")).content.decode()
        assert html.count("Desistência depois do prazo") == 1
        assert "Pendente" in html


class TestPwaAndSecurity:
    def test_manifest(self, client: Client) -> None:
        response = client.get(reverse("manifest"))
        assert response["Content-Type"] == "application/manifest+json"
        data = response.json()
        assert data["name"] == "Babinha de Domingo"
        assert data["short_name"] == "Babinha"
        assert data["lang"] == "pt-BR"
        assert data["display"] == "standalone"
        assert {icon["sizes"] for icon in data["icons"]} >= {"192x192", "512x512"}
        assert "multas" not in data["description"]
        assert "ranking" in data["description"]

    def test_service_worker_is_served_from_root(self, client: Client) -> None:
        response = client.get(reverse("service_worker"))
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/javascript")
        assert response["Service-Worker-Allowed"] == "/"
        assert "/offline/" in response.content.decode()

    def test_static_icons_exist(self, client: Client) -> None:
        for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png"):
            assert client.get(f"/static/icons/{name}").status_code == 200

    def test_health(self, client: Client) -> None:
        assert client.get(reverse("health")).json() == {"status": "ok"}

    def test_security_headers(self, client: Client) -> None:
        response = client.get(reverse("accounts:login"))
        assert "script-src 'self'" in response["Content-Security-Policy"]
        assert response["X-Frame-Options"] == "DENY"

    def test_logout_requires_post(self, player_client: Client) -> None:
        assert player_client.get(reverse("accounts:logout")).status_code == 405
        assert player_client.post(reverse("accounts:logout")).status_code == 302
