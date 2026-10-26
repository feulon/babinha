"""Community in a real browser: a player votes for the Bola de Ouro from the home screen, sees
the result once the vote closes, finds it in the ranking and on the profile tabs; the diretoria
ends the season."""

import random
import re
from collections.abc import Callable
from datetime import datetime

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Player
from core.clock import FixedClock
from games import services as games
from matchday import services as matchday
from matchday.domain.score import Side
from tests.conftest import SP, TEST_PASSWORD, Sunday, start_game
from tests.e2e.helpers import expect_signed_in, sign_out, tab

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

KICKOFF = datetime(2026, 10, 18, 10, 5, tzinfo=SP)
AFTER_GAME = datetime(2026, 10, 18, 11, 30, tzinfo=SP)
MONDAY_LIST_OPENS = datetime(2026, 10, 19, 8, 0, tzinfo=SP)


def log_in(page: Page, base_url: str, player: Player) -> None:
    page.goto(f"{base_url}/entrar/")
    page.get_by_label("Celular ou e-mail").fill(player.phone)
    page.get_by_label("Senha").fill(TEST_PASSWORD)
    page.get_by_role("button", name="Entrar").click()
    expect_signed_in(page)


def test_vote_result_ranking_and_season(
    page: Page,
    live_server,
    clock: FixedClock,
    fill_sunday: Callable[..., Sunday],
    board_member: Player,
    president: Player,
) -> None:
    sunday = fill_sunday()
    day_id = sunday.game_day.pk
    matchday.draw(day_id, board_member, random.Random(3))
    matchday.confirm(day_id, board_member)
    clock.set(KICKOFF)
    start_game(sunday.game_day)
    match = matchday.add_match(day_id, board_member, home_team=1, away_team=2)
    scorer = match.lineup.spots.filter(team=match.home_team).select_related("player")[0].player
    for _ in range(2):
        matchday.add_goal(match.pk, board_member, side=Side.HOME, scorer=scorer.pk)
    clock.set(AFTER_GAME)
    games.finish_game_day(day_id, board_member)

    voter, first, second, third = sunday.line[:4]
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("dialog", lambda dialog: dialog.accept())
    log_in(page, live_server.url, voter)

    card = page.locator('[data-card="bola-de-ouro"]')
    expect(card).to_contain_text("votação aberta")
    card.get_by_role("link", name="Votar").click()
    expect(page.get_by_role("heading", name="Bola de Ouro", level=1)).to_be_visible()
    expect(page.get_by_label("1º lugar").locator("option", has_text=voter.name)).to_have_count(0)
    page.get_by_label("1º lugar").select_option(label=first.name)
    page.get_by_label("2º lugar").select_option(label=second.name)
    page.get_by_label("3º lugar").select_option(label=third.name)
    page.get_by_role("button", name="Votar").click()
    expect(page.locator('[data-state="votou"]')).to_be_visible()

    clock.set(MONDAY_LIST_OPENS)
    page.reload()
    expect(page.locator('[data-state="resultado"]')).to_contain_text(first.name)
    expect(page.get_by_text("5 pts")).to_be_visible()

    page.get_by_role("navigation", name="Navegação principal").get_by_role(
        "link", name="Turma", exact=True
    ).click()
    page.get_by_role("navigation", name="Comunidade").get_by_role(
        "link", name="Ranking", exact=True
    ).click()
    expect(page.get_by_role("heading", name="Ranking", level=1)).to_be_visible()
    top = page.locator("tbody tr").first
    expect(top).to_contain_text(first.name)
    page.get_by_role("link", name="Gols", exact=True).click()
    expect(page.locator("tbody tr").first).to_contain_text(scorer.name)
    page.get_by_role("link", name=scorer.name).click()
    expect(page.get_by_role("heading", name=scorer.name, level=1)).to_be_visible()
    expect(page.get_by_text("Últimos domingos")).to_be_visible()
    # The tabs swap in place (htmx) and keep the address in sync.
    nav = page.get_by_role("navigation", name="Seções do perfil")
    nav.get_by_role("link", name="Domingos").click()
    expect(page.get_by_role("heading", name="Domingo a domingo")).to_be_visible()
    expect(page).to_have_url(re.compile(r"\?aba=domingos(&|$)"))
    expect(page.locator(".scoreline").first).to_contain_text("2×0")  # noqa: RUF001
    nav.get_by_role("link", name="Bolas").click()
    expect(page.get_by_role("heading", name="Pódios")).to_be_visible()
    nav.get_by_role("link", name="Origem").click()
    expect(page.get_by_text("Indicado por")).to_be_visible()
    page.go_back()
    expect(page.get_by_role("heading", name="Pódios")).to_be_visible()

    # The directory finds a player by name and opens the profile; the avatar opens your own.
    tab_bar = page.get_by_role("navigation", name="Navegação principal")
    tab_bar.get_by_role("link", name="Turma").click()
    page.get_by_label("Buscar por nome").fill(first.name)
    expect(page.locator(".person")).to_have_count(1)
    page.locator(".person").click()
    expect(page.get_by_role("heading", name=first.name, level=1)).to_be_visible()
    expect(page.locator("[data-private]")).to_have_count(0)
    page.get_by_role("link", name="Meu perfil").click()
    expect(page.get_by_role("heading", name=voter.name, level=1)).to_contain_text("(você)")
    expect(page.locator('[data-private="payments"]')).to_be_visible()

    sign_out(page)
    assert errors == []  # no CSP violations or script errors on any screen
    log_in(page, live_server.url, president)  # only the presidente ends a season
    tab(page, "Diretoria")
    page.locator(".tiles").get_by_role("link", name="Temporada").click()
    page.get_by_role("button", name="Encerrar temporada 1").click()
    expect(page.get_by_text("Começou a temporada 2.")).to_be_visible()
    expect(page.get_by_role("heading", name="Temporada 2", level=1)).to_be_visible()
    expect(page.get_by_text("Temporada 1 · até")).to_be_visible()
