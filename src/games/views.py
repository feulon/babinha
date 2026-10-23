from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_http_methods, require_POST

from accounts.invites import invite_link
from core.htmx import is_htmx
from core.models import ClubSettings
from finance.views import payments_context
from fines import services as fines
from games import services
from games.domain.roster import ListError
from games.messages import for_actor
from games.selectors import build_week_view


def _render_home(
    request: HttpRequest, *, partial: bool = False, oob_messages: bool = True
) -> HttpResponse:
    """The home screen; `partial` renders only the swappable `#home` block for htmx.

    `oob_messages` also delivers the pending flash messages with the block; a plain refresh
    leaves them alone."""
    view = build_week_view(services.current_game_day(), request.user)
    # The partial is answered from the join URL, so the receipt form must return to the home.
    # The card leads to the Sunday of "Acompanhar o domingo": once it is over (the same moment the
    # Bola de Ouro opens) the card says so instead of offering to follow it.
    followed = services.follow_game_day()
    context = {
        "week": view,
        "hero_day": followed,
        "hero_over": services.sunday_over(followed),
        "hero_follow_open": services.sunday_arrived(followed),
        "venue_name": ClubSettings.load().venue_name,
        "next_url": reverse("games:home"),
        "invite_link": invite_link(request, request.user.display_name),
        **payments_context(request),
    }
    if partial:
        return render(request, "games/_home.html", {**context, "oob_messages": oob_messages})
    return render(request, "games/home.html", context)


@login_required
def home(request: HttpRequest) -> HttpResponse:
    if is_htmx(request):  # the screen refreshing itself
        response = _render_home(request, partial=True, oob_messages=False)
    else:
        response = _render_home(request)
    patch_vary_headers(response, ["HX-Request"])
    return response


@login_required
@require_POST
def join(request: HttpRequest) -> HttpResponse:
    game_day = services.current_game_day()
    try:
        result = services.join_list(game_day.pk, request.user)
    except ListError as error:
        messages.error(request, error.message)
    else:
        for level, text in for_actor(result, request.user.pk):
            messages.add_message(request, level, text)
    if is_htmx(request):
        return _render_home(request, partial=True)
    return redirect("games:home")


@login_required
@require_http_methods(["GET", "POST"])
def leave(request: HttpRequest) -> HttpResponse:
    game_day = services.current_game_day()
    if request.method == "GET":
        view = build_week_view(game_day, request.user)
        if not view.can_leave:
            return redirect("games:home")
        context = {"week": view}
        if view.leaving_is_fined:
            context["fine_ordinal"], context["fine_amount"] = fines.next_fine(request.user)
        return render(request, "games/leave.html", context)
    try:
        result = services.leave_list(game_day.pk, request.user)
    except ListError as error:
        messages.error(request, error.message)
    else:
        for level, text in for_actor(result, request.user.pk):
            messages.add_message(request, level, text)
    return redirect("games:home")
