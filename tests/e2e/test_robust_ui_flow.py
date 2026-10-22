"""The interface under stress in a real browser: double taps, answers far down the page, sessions
that ended, servers that fail."""

from collections.abc import Callable
from datetime import datetime

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Player
from core.clock import FixedClock
from tests.conftest import SP, TEST_PASSWORD, Sunday
from tests.e2e.helpers import expect_signed_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

DAY_URL = "/diretoria/domingo/2026-10-18/"


def log_in(page: Page, base_url: str, player: Player) -> None:
    page.goto(f"{base_url}/entrar/")
    page.get_by_label("Celular ou e-mail").fill(player.phone)
    page.get_by_label("Senha").fill(TEST_PASSWORD)
    page.get_by_role("button", name="Entrar").click()
    expect_signed_in(page)


def test_double_tap_on_a_plain_form_creates_one_player(
    page: Page, live_server, board_member: Player
) -> None:
    log_in(page, live_server.url, board_member)
    page.goto(f"{live_server.url}/diretoria/jogadores/novo/")
    page.get_by_label("Nome").fill("Duplo Clique")
    page.get_by_label("Celular").fill("(11) 91234-5678")
    page.get_by_label("E-mail").fill("duplo@example.com")

    posts: list[str] = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)

    # A second tap 40 ms after the first, faster than Playwright's own double click.
    page.get_by_role("button", name="Criar jogador").evaluate(
        "b => { b.click(); setTimeout(() => b.click(), 40); }"
    )

    expect(page.locator("[data-new-login]")).to_contain_text("Login de Duplo Clique")
    assert len(posts) == 1
    assert Player.objects.filter(name="Duplo Clique").count() == 1


def test_answer_to_an_action_far_down_the_page_stays_in_view(
    page: Page,
    live_server,
    fill_sunday: Callable[..., Sunday],
    board_member: Player,
    clock: FixedClock,
) -> None:
    fill_sunday(line=20, goalkeepers=2)
    clock.set(datetime(2026, 10, 18, 10, 5, tzinfo=SP))
    page.on("dialog", lambda dialog: dialog.accept())
    log_in(page, live_server.url, board_member)
    page.goto(f"{live_server.url}{DAY_URL}")
    page.locator("#passo-presenca > summary").click()
    for group in page.locator("#passo-presenca details > summary").all():
        group.click()
    button = page.locator("#presenca-goleiros").get_by_role("button", name="Faltou").last
    button.scroll_into_view_if_needed()
    page.evaluate("window.__noReload = true")

    button.click()

    message = page.locator("#messages .message").first
    expect(message).to_contain_text("Falta registrada")
    expect(message).to_be_in_viewport()
    assert page.evaluate("window.scrollY") > 0
    assert page.evaluate("window.__noReload") is True


def test_form_error_is_brought_into_view(
    page: Page, live_server, board_member: Player, make_player: Callable[..., Player]
) -> None:
    taken = make_player("Já Existe")
    log_in(page, live_server.url, board_member)
    page.goto(f"{live_server.url}/diretoria/jogadores/novo/")
    page.get_by_label("Nome").fill("Outro Nome")
    page.get_by_label("Celular").fill(taken.phone)
    page.get_by_label("E-mail").fill("outro@example.com")

    page.get_by_role("button", name="Criar jogador").click()

    error = page.locator(".errorlist").first
    expect(error).to_be_visible()
    expect(error).to_be_in_viewport()


def test_ended_session_sends_an_action_to_the_login_page(
    page: Page, live_server, clock: FixedClock, make_player: Callable[..., Player]
) -> None:
    log_in(page, live_server.url, make_player("Garrincha"))
    page.context.clear_cookies(name="sessionid")

    page.get_by_role("button", name="Entrar na lista").click()

    expect(page).to_have_url(f"{live_server.url}/entrar/?next=%2F")
    expect(page.get_by_role("button", name="Entrar", exact=True)).to_be_visible()


@pytest.mark.parametrize(
    ("status", "text"),
    [(500, "O servidor teve um problema"), (403, "Sua sessão expirou")],
)
def test_failed_action_says_so(
    page: Page,
    live_server,
    clock: FixedClock,
    make_player: Callable[..., Player],
    status: int,
    text: str,
) -> None:
    log_in(page, live_server.url, make_player("Garrincha"))
    page.route("**/lista/entrar/", lambda route: route.fulfill(status=status, body="x"))

    page.get_by_role("button", name="Entrar na lista").click()

    expect(page.locator("#messages .message--error")).to_contain_text(text)


def test_lost_connection_says_so(
    page: Page, live_server, clock: FixedClock, make_player: Callable[..., Player]
) -> None:
    log_in(page, live_server.url, make_player("Garrincha"))
    page.route("**/lista/entrar/", lambda route: route.abort())

    page.get_by_role("button", name="Entrar na lista").click()

    expect(page.locator("#messages .message--error")).to_contain_text("Sem conexão, tente de novo")


def test_action_survives_a_csrf_cookie_that_turned_over(
    page: Page, live_server, clock: FixedClock, make_player: Callable[..., Player]
) -> None:
    log_in(page, live_server.url, make_player("Garrincha"))
    # A login in another tab rotates the cookie; the token printed in this page is now stale.
    page.context.add_cookies([{"name": "csrftoken", "value": "a" * 32, "url": live_server.url}])

    page.get_by_role("button", name="Entrar na lista").click()

    expect(page.get_by_text("Você está na lista deste domingo!")).to_be_visible()
