from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.cache import patch_vary_headers

from core.htmx import is_htmx
from games import services as games
from games.models import GameDay
from matchday.follow import follow_of
from matchday.selectors import day_view, past_days

HISTORY_SIZE = 8


def _existing_sunday_or_404(day: str) -> GameDay:
    try:
        sunday = date.fromisoformat(day)
    except ValueError as exc:
        raise Http404 from exc
    game_day = GameDay.objects.filter(date=sunday).first()
    if game_day is None:
        raise Http404
    return game_day


@login_required
def day(request: HttpRequest, day: str | None = None) -> HttpResponse:
    focus = games.follow_game_day()
    game_day = focus if day is None else _existing_sunday_or_404(day)
    context = {
        "view": day_view(game_day, request.user),
        "follow": follow_of(game_day, request.user),
        "history": [
            past
            for past in past_days(before=focus.date, limit=HISTORY_SIZE)
            if past.pk != game_day.pk
        ],
        "is_current": game_day.pk == focus.pk,
    }
    # The page refreshes itself over htmx, which asks for just the swappable block.
    response = render(
        request, "matchday/_day.html" if is_htmx(request) else "matchday/day.html", context
    )
    patch_vary_headers(response, ["HX-Request"])
    return response
