"""Demo mode in a real browser at phone width: the Sunday processes (draw and confirm the teams,
play, finish, charge) work on any day, which here is whatever day the suite runs on."""

import re

import pytest
from django.core.management import call_command
from playwright.sync_api import Page, expect

from core import demo
from core.models import DEFAULT_LATE_FINE_15
from demo.players import DEMO_PASSWORD
from finance.models import Charge
from fines.models import Fine, FineReason
from tests.e2e.helpers import expect_signed_in, tab

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def demo_on(settings) -> None:
    settings.BABA_DEMO = True
    call_command("demo", "lista", verbosity=0)


def log_in(page: Page, base_url: str, email: str) -> None:
    page.goto(f"{base_url}/entrar/")
    page.get_by_label("Celular ou e-mail").fill(email)
    page.get_by_label("Senha").fill(DEMO_PASSWORD)
    page.get_by_role("button", name="Entrar").click()
    expect_signed_in(page)


def open_sunday(page: Page) -> None:
    """Build the Sunday: Diretoria tab, then Montar o domingo in the Painel."""
    tab(page, "Diretoria")
    page.locator(".tiles").get_by_role("link", name="Montar o domingo").click()


def expect_banner(page: Page) -> None:
    banner = page.locator("[data-demo-banner]")
    expect(banner).to_have_text(demo.BANNER)
    expect(banner).to_be_in_viewport()


def test_the_diretoria_runs_the_sunday_from_teams_to_charges(page: Page, live_server) -> None:
    page.on("dialog", lambda dialog: dialog.accept())
    log_in(page, live_server.url, "presidente@demo.baba")
    open_sunday(page)
    expect_banner(page)

    page.get_by_role("link", name="Montar times").click()
    page.get_by_role("button", name="Sortear times").click()
    expect(page.get_by_text("Times sorteados.")).to_be_visible()
    page.get_by_role("button", name="Confirmar times").click()
    expect(page.get_by_text("Times confirmados: os jogadores já podem ver.")).to_be_visible()

    open_sunday(page)
    page.get_by_role("link", name="Abrir tabela de jogos").click()
    page.get_by_role("button", name="Começar Time A x Time B").click()
    expect(page.get_by_text("Time A x Time B em campo.")).to_be_visible()
    # The demo releases the calendar, but the Sunday still starts only when the diretor says so.
    page.get_by_role("button", name="Iniciar o domingo").click()
    expect(page.get_by_text("Domingo iniciado.")).to_be_visible()

    page.locator("form[action*='/gol/home/assistencia/']").get_by_role("button").first.click()
    page.locator("[data-goal-assist]").get_by_role("button", name="Não se aplica").click()
    expect(page.locator(".flash, [role=status]").filter(has_text="Gol do Time")).to_be_visible()

    # With a game on court the Diretoria tab opens that game; the steps are two screens up.
    tab(page, "Diretoria")
    expect(page.get_by_role("heading", name="Jogo 1", level=1)).to_be_visible()
    page.get_by_role("link", name=re.compile(r"Tabela de")).click()
    page.get_by_role("link", name=re.compile(r"Montar o domingo")).click()
    page.locator("#passo-fim summary").click()
    page.get_by_role("button", name="Finalizar jogo e cobrar").click()
    expect(
        page.get_by_text("Jogo finalizado. As cobranças do domingo foram abertas.")
    ).to_be_visible()


def test_the_late_fine_works_beside_another_app_on_the_same_host(
    page: Page, live_server, settings
) -> None:
    """The demo names its CSRF cookie apart from the regular app, and a plain `csrftoken` cookie
    left on localhost by another app (the dev stack) is not the demo's: the scripts must not send
    it, or every tap on the clock and on Multar por atraso is refused."""
    settings.CSRF_COOKIE_NAME = "baba_demo_csrftoken"
    page.on("dialog", lambda dialog: dialog.accept())
    log_in(page, live_server.url, "presidente@demo.baba")
    page.context.add_cookies([{"name": "csrftoken", "value": "a" * 32, "url": live_server.url}])
    open_sunday(page)

    presence = page.locator("#passo-presenca")
    presence.locator("> summary").click()
    presence.get_by_role("button", name="Iniciar o domingo").click()
    expect(page.get_by_text("Domingo iniciado.")).to_be_visible()
    if presence.get_attribute("open") is None:
        presence.locator("> summary").click()
    page.locator("#presenca-linha > summary").click()
    page.locator("#presenca-linha form[action*=atraso] button").first.click()
    expect(page.locator("#multar-atraso .late-row")).to_contain_text("15 min")

    page.locator("#passo-fim > summary").click()
    page.get_by_role("button", name="Multar 1 jogador · R$ 10,00").click()
    expect(page.get_by_text("1 jogador multado por atraso: R$ 10,00.")).to_be_visible()
    expect(page.locator("#messages .message--error")).to_have_count(0)
    fine = Fine.objects.get(reason=FineReason.LATE)
    assert fine.amount == DEFAULT_LATE_FINE_15
    assert Charge.objects.get(fine=fine).amount == DEFAULT_LATE_FINE_15
