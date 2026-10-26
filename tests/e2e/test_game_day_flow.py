"""Game day in a real browser: the diretoria draws, names and confirms the teams, builds the
match table and records a result; a player then sees their team and the score."""

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Player
from core.clock import FixedClock
from matchday.models import Goal, LineupSpot
from tests.conftest import TEST_PASSWORD, Sunday
from tests.e2e.helpers import expect_signed_in, sign_out, tab

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

# Sunday before 10h: goals wait for the diretoria to start the Sunday, not for the clock.
EARLY_SUNDAY = datetime(2026, 10, 18, 9, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))


def log_in(page: Page, base_url: str, player: Player) -> None:
    page.goto(f"{base_url}/entrar/")
    page.get_by_label("Celular ou e-mail").fill(player.phone)
    page.get_by_label("Senha").fill(TEST_PASSWORD)
    page.get_by_role("button", name="Entrar").click()
    expect_signed_in(page)


def test_draw_confirm_and_score(
    page: Page,
    live_server,
    clock: FixedClock,
    fill_sunday: Callable[..., Sunday],
    board_member: Player,
) -> None:
    fill_sunday(line=21, goalkeepers=1)
    page.on("dialog", lambda dialog: dialog.accept())
    log_in(page, live_server.url, board_member)

    tab(page, "Diretoria")
    page.locator(".tiles").get_by_role("link", name="Times").click()
    expect(page.locator('[data-lineup="none"]')).to_be_visible()
    page.get_by_role("button", name="Sortear times").click()
    expect(page.get_by_text("Times sorteados.")).to_be_visible()
    expect(page.locator('[data-lineup="draft"]')).to_be_visible()
    expect(page.locator(".spot[data-team]")).to_have_count(5)  # four teams and the goal
    expect(page.locator('[data-team="No gol"]')).to_contain_text("Goleiros:")

    page.get_by_text("Nomes dos times").click()
    page.get_by_label("Time 1").fill("Azul")
    page.get_by_role("button", name="Salvar nomes").click()
    expect(page.get_by_text("Nomes dos times salvos.")).to_be_visible()

    page.get_by_role("button", name="Confirmar times").click()
    expect(page.locator('[data-lineup="confirmed"]')).to_be_visible()

    page.get_by_role("link", name="Tabela de jogos").click()
    expect(page.get_by_text("Primeiro jogo")).to_be_visible()
    page.get_by_role("button", name="Começar Azul x Time B").click()
    expect(page.get_by_text("Azul x Time B em campo.")).to_be_visible()

    clock.set(EARLY_SUNDAY)
    page.reload()
    expect(page.get_by_role("heading", name="Jogo 1")).to_be_visible()
    waiting = page.locator('[data-state="nao-iniciado"]')
    expect(waiting.get_by_text("Inicie o domingo")).to_be_visible()
    expect(page.locator("[data-match-timer]")).to_have_count(0)
    expect(page.get_by_role("heading", name="Gol do Azul")).to_have_count(0)
    waiting.get_by_role("button", name="Iniciar o domingo").click()
    expect(page.get_by_text("Domingo iniciado.")).to_be_visible()
    expect(page.get_by_role("heading", name="Jogo 1")).to_be_visible()
    expect(waiting).to_have_count(0)
    timer = page.locator("[data-match-timer]")
    expect(timer.locator(".timer__clock")).to_have_text("7:00")
    timer.get_by_role("button", name="Iniciar").click()
    expect(timer.get_by_role("button", name="Pausar")).to_be_visible()

    azul = [s.player for s in LineupSpot.objects.filter(team__name="Azul").order_by("player__name")]
    # One tap on the scorer's name asks who gave the assist: a player, nobody or not applicable.
    azul_card = page.locator("section.card", has=page.get_by_role("heading", name="Gol do Azul"))
    azul_card.get_by_role("button", name=azul[0].name, exact=True).click()
    expect(page.get_by_text("Quem deu a assistência?")).to_be_visible()
    page.locator("[data-goal-assist]").get_by_role("button", name=azul[1].name, exact=True).click()
    expect(page.get_by_text("Gol do Azul!")).to_be_visible()
    expect(page.get_by_label("Placar")).to_have_text("1x0")

    azul_card = page.locator("section.card", has=page.get_by_role("heading", name="Gol do Azul"))
    azul_card.get_by_role("button", name=azul[1].name, exact=True).click()
    page.locator("[data-goal-assist]").get_by_role("button", name="Nenhum").click()
    expect(page.locator('[data-match-state="encerrada"]')).to_be_visible()
    expect(page.get_by_role("button", name="Começar Azul x Time C")).to_be_visible()
    expect(page.get_by_label("Placar")).to_have_text("2x0")
    assert Goal.objects.filter(match__home_team__name="Azul").count() == 2

    # The match is over, and the first goal is still editable: it was credited to the wrong player.
    page.locator(".goals li").first.get_by_role("link", name="Editar").click()
    page.get_by_label("Autor do gol").select_option(label=azul[2].name)
    page.get_by_role("button", name="Salvar gol").click()
    expect(page.get_by_text("Gol corrigido.")).to_be_visible()
    expect(page.locator(".goals li").first).to_contain_text(azul[2].name)
    expect(page.get_by_label("Placar")).to_have_text("2x0")

    sign_out(page)
    log_in(page, live_server.url, azul[0])
    page.get_by_role("link", name="Acompanhar o domingo").click()
    expect(page.get_by_text("Você está no Azul")).to_be_visible()
    expect(page.locator("[data-presence]")).to_have_text("Você está na lista.")
    roster = page.locator("#quem-vai")
    expect(roster).not_to_have_attribute("open", "")
    roster.locator("summary").click()
    expect(roster.get_by_role("heading", name="Goleiros", exact=True)).to_be_visible()
    expect(roster.get_by_role("heading", name="Linha", exact=True)).to_be_visible()
    expect(roster.get_by_role("link", name=f"{azul[0].name} (você)", exact=True)).to_be_visible()
    # The table is a strip of matches; the last played one is open below it, with its goals.
    expect(page.locator(".faixa__tile-score")).to_have_text("2 x 0")
    expect(page.locator('.faixa__tile[aria-selected="true"]')).to_have_count(1)
    expect(page.locator(".faixa__panel:not([hidden]) .faixa__score")).to_contain_text("2")
    expect(page.get_by_text(f"ass. {azul[1].name}")).to_be_visible()
    # A live refresh swaps the block; the match the player opened stays open.
    page.locator(".faixa__tile").first.click()
    page.evaluate("htmx.trigger(document.getElementById('day'), 'live-refresh')")
    page.wait_for_function("document.querySelectorAll('.faixa__tile').length > 0")
    page.wait_for_timeout(500)
    expect(page.locator('.faixa__tile[aria-selected="true"]')).to_have_count(1)
    expect(page.locator(".faixa__panel:not([hidden])")).to_have_count(1)
    expect(page.get_by_text(f"ass. {azul[1].name}")).to_be_visible()
    expect(page.get_by_text("Pote")).to_have_count(0)
