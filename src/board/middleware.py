from collections.abc import Callable

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse

from core import clock
from games.domain.schedule import matchday_sunday


class SundayUnavailable(Http404):
    """A diretoria address names a day that is not a Sunday of the baba."""


class SundayUnavailableMiddleware:
    """Answers `SundayUnavailable` with a page in the app's layout and a way back."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        if not isinstance(exception, SundayUnavailable):
            return None
        current = matchday_sunday(clock.now())
        context = {
            "current": current,
            "current_url": reverse("board:game_day", kwargs={"day": current.isoformat()}),
        }
        return render(request, "board/sunday_unavailable.html", context, status=404)
