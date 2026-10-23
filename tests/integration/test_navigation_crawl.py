"""Regression walk over every GET page of the app, as a player and as the diretoria. Three
invariants keep the navigation honest:

- every page of the signed-in app has a way out: a lit tab or an exit button;
- every link on a page leads to an address that exists (no dead ends, no 404);
- no page has a loose back link (an underlined "Mais" above the title): the way back is the tab
  bar, and a screen opened with `?next=` keeps the tab of the screen it came from lit.
"""

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from html import unescape

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import Resolver404, resolve, reverse

from accounts.models import Player
from core.clock import FixedClock
from finance import services as payments
from finance.models import Charge, ChargeKind, Expense
from games import services as games
from matchday import services as matchday
from matchday.models import Match
from tests.conftest import PNG_BYTES, Sunday, end_game

pytestmark = pytest.mark.django_db

MODES = ("player", "board")

# Screens that are a dead end by design: the contact page of someone without a phone, the full
# photo (it has "Fechar"), the live pages that close themselves.
NO_EXIT_NEEDED = {"accounts:photo_page"}

LINK = re.compile(r'<a\b[^>]*?href="([^"]*)"', re.S)
LOOSE_BACK = re.compile(r'class="board-back"|class="eyebrow"><a |<a [^>]*>\s*\u2039 ')
LIT_TAB = re.compile(r'class="tabbar__item is-active')
LIT_MAIS = re.compile(r'<a href="/mais/" class="tabbar__item is-active')
EXIT_BUTTON = re.compile(r'<a [^>]*href="[^"]*"[^>]*>\s*(Voltar|Cancelar|Fechar)[^<]*</a>')


@pytest.fixture
def world(
    fill_sunday: Callable[..., Sunday],
    board_member: Player,
    president: Player,
    club_values,
    clock: FixedClock,
) -> dict[str, object]:
    sunday = fill_sunday(line=20, goalkeepers=2)
    matchday.draw(sunday.game_day.pk, board_member)
    matchday.confirm(sunday.game_day.pk, board_member)
    matchday.start_next_match(sunday.game_day.pk, board_member)
    games.join_list(sunday.game_day.pk, board_member)
    end_game(sunday.game_day)
    charge = Charge.objects.filter(kind=ChargeKind.GAME).first()
    receipt = payments.submit_receipt(
        charge.pk, charge.player, SimpleUploadedFile("pix.png", PNG_BYTES)
    )
    expense = Expense.objects.create(
        amount=Decimal("50.00"),
        description="Bola",
        date=date(2026, 10, 13),
        created_by=board_member,
        updated_by=board_member,
    )
    return {
        "sunday": sunday,
        "day": sunday.game_day.date.isoformat(),
        "other": sunday.line[0],
        "match": Match.objects.first(),
        "receipt": receipt,
        "expense": expense,
    }


def screens(world: dict[str, object]) -> list[str]:
    day = world["day"]
    other = world["other"].pk
    return [
        reverse("games:home"),
        reverse("games:leave"),
        reverse("more"),
        reverse("rules"),
        reverse("about"),
        reverse("feedback"),
        reverse("tutorial"),
        reverse("matchday:today"),
        reverse("matchday:day", kwargs={"day": day}),
        reverse("finance:payments"),
        reverse("finance:report"),
        reverse("finance:report_month", kwargs={"month": "2026-10"}),
        reverse("fines:mine"),
        reverse("community:players"),
        reverse("community:ranking"),
        reverse("community:gold_ball"),
        reverse("community:gold_ball_day", kwargs={"day": day}),
        reverse("community:player", kwargs={"player_id": other}),
        reverse("community:player", kwargs={"player_id": other}) + "?aba=origem",
        reverse("accounts:photo_edit"),
        reverse("accounts:referrer"),
        reverse("accounts:contact"),
        reverse("accounts:password_change"),
        reverse("board:dashboard"),
        reverse("board:tutorial"),
        reverse("board:sunday_tutorial"),
        reverse("board:players"),
        reverse("board:player_new"),
        reverse("board:player_edit", kwargs={"player_id": other}),
        reverse("board:game_day", kwargs={"day": day}),
        reverse("board:teams", kwargs={"day": day}),
        reverse("board:matches", kwargs={"day": day}),
        reverse("board:match", kwargs={"match_id": world["match"].pk}),
        reverse("board:fines"),
        reverse("board:settings"),
        reverse("board:receipts"),
        reverse("board:receipt", kwargs={"receipt_id": world["receipt"].pk}),
        reverse("board:pending"),
        reverse("board:reminders"),
        reverse("board:expenses"),
        reverse("board:expenses_month", kwargs={"month": "2026-10"}),
        reverse("board:expense_edit", kwargs={"expense_id": world["expense"].pk}),
        reverse("board:cash"),
        reverse("board:cash_month", kwargs={"month": "2026-10"}),
        reverse("community:season"),
    ]


def client_as(mode: str, player: Player, board: Player) -> Client:
    client = Client()
    client.force_login(player if mode == "player" else board)
    return client


def view_name(url: str) -> str:
    return resolve(url.split("?", 1)[0]).view_name


def internal_links(html: str) -> list[str]:
    links = []
    for raw in LINK.findall(html):
        href = unescape(raw)
        if href.startswith("/") and not href.startswith("//"):
            links.append(href)
    return links


@pytest.mark.parametrize("mode", MODES)
def test_every_page_has_a_way_out_and_only_live_links(
    mode: str, world: dict[str, object], make_player, board_member: Player
) -> None:
    client = client_as(mode, world["other"], board_member)
    problems = []
    for url in screens(world):
        is_board_page = view_name(url).startswith("board:") or view_name(url) == "community:season"
        response = client.get(url)
        if response.status_code == 403:
            assert mode == "player", (mode, url)
            assert is_board_page, (mode, url)
            continue
        assert response.status_code == 200, (mode, url, response.status_code)
        html = response.content.decode()
        if view_name(url) in NO_EXIT_NEEDED:
            continue
        if LOOSE_BACK.search(html):
            problems.append(f"{mode}: {url} has a loose back link")
        if not (LIT_TAB.search(html) or EXIT_BUTTON.search(html)):
            problems.append(f"{mode}: {url} has no tab and no exit button")
        for link in internal_links(html):
            path = link.split("?", 1)[0].split("#", 1)[0]
            try:
                resolve(path)
            except Resolver404:
                if not path.startswith(("/static/", "/media/")):
                    problems.append(f"{mode}: {url} links to {link}, which does not exist")
                continue
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("mode", MODES)
def test_a_screen_opened_with_next_keeps_the_tab_it_came_from(
    mode: str, world: dict[str, object], board_member: Player
) -> None:
    client = client_as(mode, world["other"], board_member)
    origin = reverse("more")
    opened_with_next = [
        reverse("community:player", kwargs={"player_id": world["other"].pk}),
        reverse("rules"),
        reverse("about"),
        reverse("finance:payments"),
        reverse("fines:mine"),
        reverse("matchday:today"),
        reverse("community:ranking"),
        reverse("community:gold_ball"),
    ]
    if mode != "player":
        opened_with_next += [
            reverse("board:receipt", kwargs={"receipt_id": world["receipt"].pk}),
            reverse("board:player_edit", kwargs={"player_id": world["other"].pk}),
        ]
    for url in opened_with_next:
        html = client.get(url, {"next": origin}).content.decode()
        assert LIT_MAIS.search(html), f"{mode}: {url} does not keep Mais lit"
        assert not LOOSE_BACK.search(html), f"{mode}: {url} has a loose back link"
