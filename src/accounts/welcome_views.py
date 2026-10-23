from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts import welcome
from core.decorators import board_required


def _screen(request: HttpRequest, kind: str, step: int) -> HttpResponse:
    if not 1 <= step <= welcome.STEPS:
        raise Http404
    welcome.mark_seen(request.user, kind)
    context = {
        "kind": kind,
        "nav": welcome.navigation(request.user, kind, step),
        "steps": range(1, welcome.STEPS + 1),
        "fullscreen": True,
        **welcome.week_context(),
    }
    return render(request, f"accounts/welcome/{kind}_{step}.html", context)


@login_required
@require_GET
def player_screen(request: HttpRequest, step: int) -> HttpResponse:
    return _screen(request, welcome.PLAYER, step)


@board_required
@require_GET
def board_screen(request: HttpRequest, step: int) -> HttpResponse:
    return _screen(request, welcome.BOARD, step)


@login_required
@require_POST
def skip(request: HttpRequest) -> HttpResponse:
    """Pular, from any screen: the set counts as seen and the person goes on to the app."""
    kind = welcome.PLAYER
    if request.POST.get("set") == welcome.BOARD and request.user.is_board:
        kind = welcome.BOARD
    welcome.mark_seen(request.user, kind)
    return redirect(welcome.exit_url(kind))
