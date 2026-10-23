"""Signup -> join the list -> logout -> login, in a real browser against the live server."""

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Player
from core.clock import FixedClock
from games.models import ListEntry
from tests.conftest import PNG_BYTES
from tests.e2e.helpers import sign_out

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_signup_login_and_join_the_list(
    page: Page, live_server, president: Player, clock: FixedClock
) -> None:
    page.goto(f"{live_server.url}/cadastro/")
    page.get_by_label("Nome ou apelido").fill("Sócrates")
    phone = page.get_by_label("Celular (com DDD)")
    phone.press_sequentially("1198765432199")  # the mask shows the DDD and stops at 11 digits
    expect(phone).to_have_value("(11) 98765-4321")
    page.get_by_label("E-mail").fill("socrates@example.com")
    page.get_by_label("Quem indicou você").fill(president.name)
    page.get_by_label("Senha (4 caracteres)").fill("dr10")
    page.get_by_label("Repita a senha").fill("dr10")
    page.get_by_label("Li e estou ciente das regras").check()
    page.get_by_role("button", name="Criar conta").click()

    expect(page.get_by_text("Bem-vindo ao Babinha, Sócrates!")).to_be_visible()
    expect(page.get_by_text("Você não está na lista")).to_be_visible()

    sign_out(page)
    expect(page).to_have_url(f"{live_server.url}/entrar/")
    page.get_by_label("Celular ou e-mail").fill("11 98765-4321")
    page.get_by_label("Senha").fill("dr10")
    page.get_by_role("button", name="Entrar").click()

    # The first sign-in lands on the rules once, whatever comes next.
    expect(page).to_have_url(f"{live_server.url}/regras/")
    page.goto(live_server.url)
    page.get_by_role("button", name="Entrar na lista").click()
    expect(page.get_by_text("Você está na lista deste domingo!")).to_be_visible()
    expect(page.locator('[data-state="lista"]')).to_be_visible()
    page.get_by_text("Lista do domingo").click()  # the rosters are folded
    expect(page.get_by_text("Sócrates (você)")).to_be_visible()

    player = Player.objects.get(phone="11987654321")
    assert player.referred_by is None
    assert player.referrer_text == president.name
    assert ListEntry.objects.get(player=player).status == "lista"

    # The free-text referral is connected to the registered player from the profile.
    page.goto(f"{live_server.url}/indicacao/")
    page.get_by_label("Buscar jogador pelo nome").fill(president.name[:4])
    page.get_by_role("button", name="Buscar").click()
    page.get_by_role("button", name="Foi essa pessoa").click()
    expect(page.get_by_text(f"Indicação ligada a {president.name}.")).to_be_visible()
    player.refresh_from_db()
    assert player.referred_by == president

    # Dropping out goes through a confirmation page.
    page.goto(live_server.url)
    page.get_by_role("link", name="Desistir").click()
    page.get_by_role("button", name="Confirmar").click()
    expect(page.get_by_text("Você saiu da lista.")).to_be_visible()
    expect(page.locator('[data-state="fora"]')).to_be_visible()


def test_signup_previews_the_chosen_photo_and_lets_it_go(
    page: Page, live_server, president: Player
) -> None:
    page.goto(f"{live_server.url}/cadastro/")
    preview = page.locator("[data-photo-preview]")
    expect(preview).to_be_hidden()
    page.get_by_label("Foto").set_input_files(
        {"name": "eu.png", "mimeType": "image/png", "buffer": PNG_BYTES}
    )
    expect(preview).to_be_visible()
    page.get_by_role("button", name="Remover foto escolhida").click()
    expect(preview).to_be_hidden()
