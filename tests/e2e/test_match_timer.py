"""The 7-minute timer on the match screen in a real browser: at zero it whistles (and vibrates),
the screen stays awake while it runs, and the diretoria can silence the whistle on this phone.

Chromium has no speaker or wake lock to listen to here, so the page gets small stand-ins that
count what the timer asks of them."""

import random
from collections.abc import Callable

import pytest
from django.urls import reverse
from playwright.sync_api import Page, expect

from accounts.models import Player
from core.clock import FixedClock
from matchday import services
from matchday.models import Match
from tests.conftest import TEST_PASSWORD, Sunday
from tests.e2e.helpers import expect_signed_in

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

SPY = """
window.spy = { sounds: 0, locksAsked: 0, locksReleased: 0 };
window.AudioContext = class {
  state = "running";
  currentTime = 0;
  destination = {};
  resume() { return Promise.resolve(); }
  createBuffer() { return {}; }
  createBufferSource() { return { connect() {}, start() {} }; }
  createGain() {
    return { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} };
  }
  createOscillator() {
    return { frequency: {}, connect() {}, start() { window.spy.sounds += 1; }, stop() {} };
  }
};
Object.defineProperty(navigator, "wakeLock", {
  configurable: true,
  value: {
    request: async () => {
      window.spy.locksAsked += 1;
      return {
        release: async () => { window.spy.locksReleased += 1; },
        addEventListener() {},
      };
    },
  },
});
"""


def log_in(page: Page, base_url: str, player: Player) -> None:
    page.goto(f"{base_url}/entrar/")
    page.get_by_label("Celular ou e-mail").fill(player.phone)
    page.get_by_label("Senha").fill(TEST_PASSWORD)
    page.get_by_role("button", name="Entrar").click()
    expect_signed_in(page)


@pytest.fixture
def match(
    fill_sunday: Callable[..., Sunday], board_member: Player, clock: FixedClock
) -> tuple[Sunday, Match]:
    sunday = fill_sunday()
    services.draw(sunday.game_day.pk, board_member, random.Random(5))
    made = services.add_match(sunday.game_day.pk, board_member, home_team=1, away_team=2)
    return sunday, made


@pytest.fixture
def match_page(page: Page, live_server, board_member: Player, match) -> Page:
    page.add_init_script(SPY)
    page.on("dialog", lambda dialog: dialog.accept())
    page.clock.install()
    log_in(page, live_server.url, board_member)
    page.goto(f"{live_server.url}{reverse('board:match', kwargs={'match_id': match[1].pk})}")
    expect(page.locator("[data-match-timer]")).to_be_visible()
    return page


def spy(page: Page, field: str) -> int:
    return page.evaluate(f"window.spy.{field}")


def test_whistle_and_wake_lock_follow_the_timer(match_page: Page) -> None:
    page = match_page
    timer = page.locator("[data-match-timer]")
    expect(timer.get_by_role("button", name="Som ligado")).to_be_visible()

    timer.get_by_role("button", name="Iniciar").click()
    expect(timer.get_by_role("button", name="Pausar")).to_be_visible()
    assert spy(page, "locksAsked") == 1

    timer.get_by_role("button", name="Pausar").click()
    expect(timer.get_by_role("button", name="Continuar")).to_be_visible()
    assert spy(page, "locksReleased") == 1

    timer.get_by_role("button", name="Continuar").click()
    assert spy(page, "locksAsked") == 2
    assert spy(page, "sounds") == 0

    page.clock.fast_forward("07:05")
    expect(timer.get_by_text("Fim do tempo")).to_be_visible()
    assert spy(page, "sounds") > 0
    assert spy(page, "locksReleased") == 2


def test_zeroing_the_timer_lets_the_screen_sleep(match_page: Page) -> None:
    timer = match_page.locator("[data-match-timer]")
    timer.get_by_role("button", name="Iniciar").click()
    assert spy(match_page, "locksAsked") == 1

    timer.get_by_role("button", name="Zerar cronômetro").click()
    expect(timer.get_by_role("button", name="Iniciar")).to_be_visible()
    assert spy(match_page, "locksReleased") == 1


def test_a_silenced_whistle_stays_silent_after_a_reload(match_page: Page) -> None:
    page = match_page
    timer = page.locator("[data-match-timer]")
    timer.get_by_role("button", name="Som ligado").click()
    expect(timer.get_by_role("button", name="Som desligado")).to_be_visible()

    page.reload()
    expect(timer.get_by_role("button", name="Som desligado")).to_be_visible()
    timer.get_by_role("button", name="Iniciar").click()
    page.clock.fast_forward("07:05")
    expect(timer.get_by_text("Fim do tempo")).to_be_visible()
    assert spy(page, "sounds") == 0

    timer.get_by_role("button", name="Som desligado").click()
    expect(timer.get_by_role("button", name="Som ligado")).to_be_visible()
