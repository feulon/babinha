"""Pay a Sunday by Pix: the player sends the receipt, the diretoria confirms it."""

from datetime import datetime

import pytest
from playwright.sync_api import Page, expect

from accounts.models import Player
from core.clock import FixedClock
from core.models import ClubSettings
from finance.models import Charge, ChargeStatus
from tests.conftest import PIX_KEY, PNG_BYTES, SP, TEST_PASSWORD
from tests.e2e.helpers import sign_out, tab

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def log_in(page: Page, base_url: str, player: Player) -> None:
    page.goto(f"{base_url}/entrar/")
    page.get_by_label("Celular ou e-mail").fill(player.phone)
    page.get_by_label("Senha").fill(TEST_PASSWORD)
    page.get_by_role("button", name="Entrar").click()


def test_pay_by_pix_and_board_confirms(
    page: Page,
    live_server,
    clock: FixedClock,
    club_values: ClubSettings,
    make_player,
    board_member: Player,
) -> None:
    player = make_player("Garrincha")
    log_in(page, live_server.url, player)
    page.get_by_role("button", name="Entrar na lista").click()
    expect(page.get_by_text("Você está na lista", exact=True)).to_be_visible()
    expect(page.locator('[data-state="pagamento"]')).to_have_count(0)

    clock.set(datetime(2026, 10, 18, 12, 31, tzinfo=SP))  # the game is over: the fee opens
    page.goto(f"{live_server.url}/")

    payment = page.locator('[data-state="pagamento"]')
    expect(payment.get_by_text("Pagamento pendente")).to_be_visible()
    expect(payment.get_by_text("R$ 30,00").first).to_be_visible()
    expect(page.get_by_text(PIX_KEY)).to_have_count(0)  # the key and the upload live in Pagamentos
    payment.get_by_role("link", name="Ver Pix").click()

    payment = page.locator('[data-state="pagamento"]')
    expect(payment.get_by_text(PIX_KEY)).to_be_visible()

    payment.get_by_label("Comprovante do Pix").set_input_files(
        {"name": "pix.png", "mimeType": "image/png", "buffer": PNG_BYTES}
    )
    payment.get_by_role("button", name="Enviar comprovante").click()
    expect(page.get_by_text("Comprovante enviado! A diretoria vai conferir.")).to_be_visible()
    expect(page.get_by_text("Comprovante em análise")).to_be_visible()

    sign_out(page)
    log_in(page, live_server.url, board_member)
    tab(page, "Diretoria")
    expect(page.get_by_text("1 para conferir")).to_be_visible()
    page.get_by_role("link", name="Comprovantes 1 para conferir").click()
    page.get_by_role("link", name="Conferir", exact=True).click()
    expect(page.get_by_role("img", name="Comprovante de Garrincha")).to_be_visible()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Confirmar pagamento").click()
    expect(page.get_by_text("Pagamento de Garrincha confirmado.")).to_be_visible()

    assert Charge.objects.get(player=player).status == ChargeStatus.PAID
    tab(page, "Mais")
    page.get_by_role("link", name="Caixa").click()
    expect(page.locator('[data-total="arrecadado"]')).to_have_text("R$ 30,00")
