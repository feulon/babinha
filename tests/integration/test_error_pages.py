"""Error pages are part of the app: pt-BR, in its layout, always with a way back to the start."""

import pytest
from django.http import HttpRequest
from django.test import Client, RequestFactory
from django.urls import reverse
from django.views.defaults import server_error

from accounts.models import Player
from core.views import server_error as core_server_error

pytestmark = pytest.mark.django_db

BACK_TO_START = "Voltar ao início"


def text(response) -> str:
    return response.content.decode()


def test_unknown_page_is_the_app_layout_in_portuguese(client: Client) -> None:
    response = client.get("/nada-aqui/")
    html = text(response)
    assert response.status_code == 404
    assert "Página não encontrada" in html
    assert BACK_TO_START in html
    assert "css/app.css" in html
    assert "Not Found" not in html


def test_player_in_the_board_area_gets_a_friendly_403(client: Client, make_player) -> None:
    client.force_login(make_player("Zico"))
    response = client.get(reverse("board:dashboard"))
    html = text(response)
    assert response.status_code == 403
    assert "Você não tem acesso a esta tela" in html
    assert BACK_TO_START in html


def test_server_error_page_stands_alone(db: None) -> None:
    request: HttpRequest = RequestFactory().get("/")
    response = server_error(request)
    html = text(response)
    assert response.status_code == 500
    assert "Algo deu errado" in html
    assert BACK_TO_START in html
    assert "css/app.css" in html


def test_the_server_error_page_shows_the_demo_banner_only_in_demo_mode(settings) -> None:
    request: HttpRequest = RequestFactory().get("/")
    settings.BABA_DEMO = False
    assert "data-demo-banner" not in text(core_server_error(request))
    settings.BABA_DEMO = True
    response = core_server_error(request)
    assert response.status_code == 500
    assert "Demonstração: os horários do dia do jogo estão liberados" in text(response)


def test_stale_csrf_token_gets_the_expired_page() -> None:
    strict = Client(enforce_csrf_checks=True)
    response = strict.post(reverse("accounts:login"), {"login": "x", "password": "y"})
    html = text(response)
    assert response.status_code == 403
    assert "A página expirou" in html
    assert BACK_TO_START in html


def test_expired_page_is_also_the_answer_to_htmx(make_player) -> None:
    player: Player = make_player("Zico")
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(player)
    response = strict.post(reverse("games:join"), HTTP_HX_REQUEST="true")
    assert response.status_code == 403
