"""Assembling the teams in a real browser: draw, rules with a conflict, adjust and confirm."""

import re
from collections.abc import Callable

import pytest
from playwright.sync_api import Locator, Page, expect

from accounts.models import Player
from matchday.models import LineupSpot
from tests.conftest import Sunday
from tests.e2e.helpers import tab
from tests.e2e.test_game_day_flow import log_in
from tests.integration.test_team_rules import invite

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def team_of(name: str) -> int | None:
    spot = LineupSpot.objects.select_related("team").get(player__name=name)
    return spot.team.number if spot.team else None


def chip(group: Locator, name: str) -> Locator:
    """The checkbox chip of a player in the new-group form."""
    return group.locator("label.pc").filter(has_text=re.compile(rf"^\s*{re.escape(name)}(?!\d)"))


def open_times(page: Page, base_url: str, board: Player) -> None:
    log_in(page, base_url, board)
    tab(page, "Diretoria")
    page.locator(".tiles").get_by_role("link", name="Times").click()
    expect(page.locator('[data-lineup="none"]')).to_be_visible()


def open_rules(page: Page) -> Locator:
    """The rules of the draw are folded away: open them and give back the new-group form."""
    page.locator('[data-card="regras"] summary').first.click()
    page.get_by_text("Juntar jogadores no mesmo time").click()
    return page.locator("#novo-grupo")


def test_rules_draw_adjust_and_confirm(
    page: Page,
    live_server,
    fill_sunday: Callable[..., Sunday],
    board_member: Player,
) -> None:
    sunday = fill_sunday(line=20, goalkeepers=2)
    rafa, bruno = sunday.line[0], sunday.line[7]
    invite(bruno, rafa)
    page.on("dialog", lambda dialog: dialog.accept())
    open_times(page, live_server.url, board_member)

    # The first-timer is held to whoever invited them, and a group is made by hand.
    expect(page.locator('[data-rule="estreantes"]')).to_be_hidden()
    group = open_rules(page)
    expect(page.locator('[data-rule="estreantes"]')).to_contain_text("Vale para 1 estreante hoje.")
    submit = group.get_by_role("button", name="Salvar grupo")
    expect(submit).to_be_disabled()
    for player in sunday.line[2:5]:
        chip(group, player.name).click()
    expect(group.locator("[data-picker-count]")).to_have_text("3 de 5")
    group.get_by_role("button", name="Salvar grupo de 3").click()
    expect(page.get_by_text("Grupo salvo.")).to_be_visible()

    # The draw keeps both rules: the pair and the group come out whole.
    page.get_by_role("button", name="Sortear times").click()
    expect(page.locator('[data-lineup="draft"]')).to_be_visible()
    expect(page.locator(".spot[data-team]")).to_have_count(5)
    assert team_of(rafa.name) == team_of(bruno.name)
    assert len({team_of(p.name) for p in sunday.line[2:5]}) == 1
    pair = page.locator(".spots .spot__row--group", has_text=rafa.name)
    expect(pair).to_contain_text(bruno.name)

    # A tap picks someone, a tap on a team moves them, and the uneven teams are said briefly.
    mover = sunday.line[10]
    page.locator(".spots a.spot__row", has_text=mover.name).click()
    expect(page.locator("[data-pick]")).to_contain_text(f"Mover {mover.name}")
    expect(page.get_by_role("button", name="Mover para cá")).to_have_count(3)
    target = 1 if team_of(mover.name) != 1 else 2
    page.locator(f'.spot[data-team="Time {chr(ord("A") + target - 1)}"]').get_by_role(
        "button", name="Mover para cá"
    ).click()
    expect(page.get_by_text(f"{mover.name} foi para o Time")).to_be_visible()
    expect(page.locator('[data-alert="imbalance"]')).to_contain_text("Times desequilibrados")
    assert team_of(mover.name) == target

    # Picking one and tapping another from a different team swaps them, and the teams even out.
    first = sunday.line[10]
    alone = [p for p in sunday.line if p not in (rafa, bruno, *sunday.line[2:5])]
    second = next(p for p in alone if team_of(p.name) not in (team_of(first.name), None))
    page.locator(".spots a.spot__row", has_text=first.name).click()
    page.locator(".spots button.is-swap", has_text=second.name).click()
    expect(page.get_by_text(f"{first.name} e {second.name} trocaram de lugar.")).to_be_visible()

    page.get_by_role("button", name="Confirmar times").click()
    expect(page.locator('[data-lineup="confirmed"]')).to_be_visible()


def test_a_conflict_is_explained_and_resolved(
    page: Page,
    live_server,
    fill_sunday: Callable[..., Sunday],
    board_member: Player,
) -> None:
    sunday = fill_sunday(line=20, goalkeepers=2)
    rafa, bruno = sunday.line[0], sunday.line[7]
    invite(bruno, rafa)
    page.on("dialog", lambda dialog: dialog.accept())
    open_times(page, live_server.url, board_member)

    group = open_rules(page)
    for player in sunday.line[:5]:
        chip(group, player.name).click()
    # The limit of five stops the picker from taking more.
    expect(group.locator("[data-picker-count]")).to_have_text("5 de 5")
    expect(chip(group, sunday.line[5].name).locator("input")).to_be_disabled()
    group.get_by_role("button", name="Salvar grupo de 5").click()

    # The way out is there, and drawing waits for it.
    conflict = page.locator('[data-alert="rules-conflict"]')
    expect(conflict).to_contain_text("Com estas regras não dá para sortear")
    expect(conflict).to_contain_text("tem 6 jogadores")
    expect(conflict).to_contain_text("sugerido")
    expect(page.get_by_role("button", name="Sortear times", exact=True)).to_have_count(0)
    expect(conflict.get_by_text(f"Deixar {bruno.name} jogar em outro time")).to_be_visible()
    conflict.get_by_role("button", name="Aplicar e sortear").click()
    expect(page.get_by_text(f"{bruno.name} vai poder jogar em outro time")).to_be_visible()
    expect(page.locator('[data-lineup="draft"]')).to_be_visible()
    assert LineupSpot.objects.count() == 20
    assert len({team_of(p.name) for p in sunday.line[:5]}) == 1
