"""In-place updates (htmx) in a real browser: the page never reloads, the screen just changes."""

import random
from collections.abc import Callable
from datetime import datetime

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Player
from core.clock import FixedClock
from games.models import ListEntry
from matchday import services
from tests.conftest import SP, TEST_PASSWORD, Sunday
from tests.e2e.helpers import expect_signed_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

NO_RELOAD = "window.__noReload = true"
AFTER_KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)


def log_in(page: Page, base_url: str, player: Player) -> None:
    page.goto(f"{base_url}/entrar/")
    page.get_by_label("Celular ou e-mail").fill(player.phone)
    page.get_by_label("Senha").fill(TEST_PASSWORD)
    page.get_by_role("button", name="Entrar").click()
    expect_signed_in(page)


def test_join_the_list_in_place(
    page: Page, live_server, clock: FixedClock, make_player: Callable[..., Player]
) -> None:
    player = make_player("Garrincha")
    log_in(page, live_server.url, player)
    expect(page.locator("[data-hero]")).to_contain_text("na espera")
    page.evaluate(NO_RELOAD)

    page.get_by_role("button", name="Entrar na lista").click()

    expect(page.locator('[data-state="lista"]')).to_be_visible()
    expect(page.get_by_text("Você está na lista deste domingo!")).to_be_visible()
    expect(page.get_by_text("Lista do domingo")).to_be_visible()
    page.get_by_text("Lista do domingo").click()
    expect(page.locator(".roster__name", has_text="Garrincha (você)")).to_be_visible()
    assert page.evaluate("window.__noReload") is True
    assert ListEntry.objects.filter(player=player).exists()


def test_board_removes_a_player_in_place(
    page: Page,
    live_server,
    fill_sunday: Callable[..., Sunday],
    board_member: Player,
) -> None:
    sunday = fill_sunday(line=3, goalkeepers=1)
    page.on("dialog", lambda dialog: dialog.accept())
    log_in(page, live_server.url, board_member)
    page.goto(f"{live_server.url}/diretoria/domingo/{sunday.game_day.date.isoformat()}/")
    expect(page.locator("#roster-board")).to_contain_text(sunday.line[0].name)
    page.evaluate(NO_RELOAD)
    page.get_by_text("Lista de jogadores (").click()  # the list is folded until asked for

    page.get_by_role("button", name=f"Retirar {sunday.line[0].name}").click()

    expect(page.get_by_text(f"{sunday.line[0].name} foi retirado.")).to_be_visible()
    expect(page.get_by_role("button", name=f"Retirar {sunday.line[0].name}")).to_have_count(0)
    # The swap keeps the list open for the next removal.
    expect(page.get_by_role("button", name=f"Retirar {sunday.line[1].name}")).to_be_visible()
    assert page.evaluate("window.__noReload") is True


def test_board_reorders_and_moves_players_in_place(
    page: Page,
    live_server,
    fill_sunday: Callable[..., Sunday],
    board_member: Player,
) -> None:
    sunday = fill_sunday(line=3, goalkeepers=1)
    third = sunday.line[2]
    page.on("dialog", lambda dialog: dialog.accept())
    log_in(page, live_server.url, board_member)
    page.goto(f"{live_server.url}/diretoria/domingo/{sunday.game_day.date.isoformat()}/")
    page.evaluate(NO_RELOAD)
    page.get_by_text("Lista de jogadores (").click()

    page.locator("summary", has_text=third.name).click()  # a tap on the name opens its moves
    page.get_by_role("button", name=f"Subir {third.name}").click()
    expect(page.get_by_text(f"{third.name} agora é o 2º da linha.")).to_be_visible()
    # The moves stay open, so the next tap moves the player again.
    page.get_by_role("button", name=f"Subir {third.name}").click()
    expect(page.get_by_text(f"{third.name} agora é o 1º da linha.")).to_be_visible()
    first_row = page.locator("#lista-jogadores .roster").nth(1).locator("li").first
    expect(first_row).to_contain_text(third.name)

    page.get_by_role("button", name=f"Passar {third.name} para o gol").click()
    expect(page.get_by_text(f"{third.name} agora é o 2º dos goleiros.")).to_be_visible()
    assert ListEntry.objects.get(player=third).position == "goleiro"
    assert page.evaluate("window.__noReload") is True


def test_board_marks_attendance_and_lateness_during_the_games(
    page: Page,
    live_server,
    fill_sunday: Callable[..., Sunday],
    board_member: Player,
    clock: FixedClock,
    club_values,
) -> None:
    sunday = fill_sunday(line=20, goalkeepers=2)
    services.draw(sunday.game_day.pk, board_member, random.Random(5))
    services.confirm(sunday.game_day.pk, board_member)
    clock.set(AFTER_KICKOFF)
    page.on("dialog", lambda dialog: dialog.accept())
    log_in(page, live_server.url, board_member)
    page.goto(f"{live_server.url}/diretoria/domingo/{sunday.game_day.date.isoformat()}/")
    page.evaluate(NO_RELOAD)

    # Attendance never blocks the games: that step is open, and the groups start folded.
    expect(page.locator("#passo-jogos")).to_have_attribute("open", "")
    group = page.locator("#presenca-time-1")
    expect(group).not_to_have_attribute("open", "")
    page.locator("#passo-presenca > summary").click()
    page.locator("#passo-presenca").get_by_role("button", name="Todos presentes").click()
    expect(page.get_by_text("22 de 22 marcados")).to_be_visible()

    # The group the diretoria opens stays open across the swaps that follow.
    group.locator("summary").click()
    late = group.locator("form[action*=atraso] button").first
    late.click()
    expect(page.locator("#multar-atraso .late-row")).to_contain_text("15 min")
    expect(group).to_have_attribute("open", "")
    late.click()
    expect(page.locator("#multar-atraso .late-row")).to_contain_text("30 min")

    # A no-show is fined when it is marked and listed apart from the late ones in the last step.
    absent = group.locator("li").nth(1)
    name = absent.locator(".roster__name").evaluate("el => el.firstChild.textContent.trim()")
    absent.get_by_role("button", name="Faltou").click()
    page.locator("#passo-fim > summary").click()
    expect(page.locator("#multar-falta")).to_contain_text(name)
    expect(page.locator("#multar-falta")).to_contain_text("R$ 10,00")
    expect(page.locator("#multar-atraso")).not_to_contain_text(name)
    page.get_by_role("button", name="Multar 1 jogador · R$ 25,00").click()
    expect(page.get_by_text("1 jogador multado por atraso: R$ 25,00.")).to_be_visible()
    expect(page.locator("#multar-atraso")).to_contain_text(
        "Todos os atrasos marcados já foram multados."
    )
    assert page.evaluate("window.__noReload") is True
