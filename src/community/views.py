from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_http_methods, require_POST

from accounts.invites import invite_link
from accounts.models import Player
from community import profile as profiles
from community import seasons, selectors, services
from community.domain.ranking import DEFAULT_METRIC, Metric
from community.domain.voting import Phase
from community.models import Season
from core import navigation
from core.decorators import board_required, presidente_required
from core.htmx import is_htmx
from games.models import GameDay

OVERALL = "geral"
METRIC_LABELS = {
    Metric.GOLD_BALLS: "Bolas de Ouro",
    Metric.GOALS: "Gols",
    Metric.ASSISTS: "Assistências",
    Metric.SUNDAYS: "Presença",
    Metric.WINS: "Vitórias",
}


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _sunday_or_404(day: str) -> GameDay:
    try:
        sunday = date.fromisoformat(day)
    except ValueError as exc:
        raise Http404 from exc
    return get_object_or_404(GameDay, date=sunday)


PLACES = [
    ("first", "1º lugar (5 pontos)"),
    ("second", "2º lugar (3 pontos)"),
    ("third", "3º lugar (2 pontos)"),
]


VOTE_RETRY_SESSION_KEY = "gold_ball_vote_retry"


def _render_gold_ball(request: HttpRequest, game_day: GameDay | None) -> HttpResponse:
    # A refused ballot comes back here through a redirect, so reloading never resends it.
    retry = request.session.pop(VOTE_RETRY_SESSION_KEY, None)
    if not (retry and game_day and retry["day"] == game_day.date.isoformat()):
        retry = {"error": None, "picks": {}}
    view = selectors.voting_view(game_day, request.user) if game_day else None
    vote_error = retry["error"]
    if vote_error and not (view and view.shows_ballot):
        # No form to put the error under (the vote closed meanwhile): a flash message instead.
        messages.error(request, vote_error)
        vote_error = None
    context = {
        "view": view,
        "past": selectors.past_gold_balls(),
        "places": [(name, label, retry["picks"].get(name)) for name, label in PLACES],
        "vote_error": vote_error,
    }
    return render(request, "community/gold_ball.html", context)


@login_required
def gold_ball(request: HttpRequest, day: str | None = None) -> HttpResponse:
    game_day = selectors.latest_voting_day() if day is None else _sunday_or_404(day)
    if is_htmx(request) and game_day is not None:
        # The vote count refreshing itself; the voting form is never swapped.
        view = selectors.voting_view(game_day, request.user)
        if view.window.phase == Phase.CLOSED:
            # Anyone watching the live count sees the result as soon as voting closes.
            return HttpResponse(status=200, headers={"HX-Refresh": "true"})
        if view.window.phase != Phase.OPEN:
            return HttpResponse(status=204)  # the count is only shown while the vote is open
        response = render(request, "community/_vote_count.html", {"view": view})
        patch_vary_headers(response, ["HX-Request"])
        return response
    return _render_gold_ball(request, game_day)


@login_required
@require_POST
def vote(request: HttpRequest, day: str) -> HttpResponse:
    game_day = _sunday_or_404(day)
    picks = {name: _int_or_none(request.POST.get(name)) for name, _ in PLACES}
    try:
        services.cast_vote(game_day.pk, request.user, **picks)
    except services.CommunityError as error:
        # Show the form again with the choices already made instead of an empty one.
        request.session[VOTE_RETRY_SESSION_KEY] = {
            "day": game_day.date.isoformat(),
            "error": error.message,
            "picks": picks,
        }
    target = reverse("community:gold_ball_day", args=[game_day.date.isoformat()])
    came_from = navigation.safe_target(request.POST.get(navigation.PARAM))
    return redirect(f"{target}?{urlencode({navigation.PARAM: came_from})}" if came_from else target)


@login_required
def ranking(request: HttpRequest) -> HttpResponse:
    all_seasons = seasons.all_seasons()
    choice = request.GET.get("temporada", "")
    if choice == OVERALL:
        season = None
    elif choice == "":
        season = seasons.current_season()
    else:
        number = _int_or_none(choice)
        season = next((s for s in all_seasons if s.number == number), None)
        if season is None:
            raise Http404

    try:
        metric = Metric(request.GET.get("ordem", DEFAULT_METRIC))
    except ValueError as exc:
        raise Http404 from exc
    context = {
        "table": selectors.season_table(season, metric),
        "seasons": reversed(all_seasons),
        "overall": season is None,
        "metrics": [(m.value, label) for m, label in METRIC_LABELS.items()],
        "metric": metric.value,
        "season_param": OVERALL if season is None else season.number,
    }
    return render(request, "community/ranking.html", context)


TABS = {
    "resumo": "Resumo",
    "domingos": "Domingos",
    "bolas": "Bolas de Ouro",
    "origem": "Origem",
}
DEFAULT_TAB = "resumo"


def _season_filter(choice: str) -> Season | None:
    """The season picked on the summary: all of them unless one is named."""
    if choice in ("", OVERALL):
        return None
    number = _int_or_none(choice)
    found = next((s for s in seasons.all_seasons() if s.number == number), None)
    if found is None:
        raise Http404
    return found


def _render_variant(request: HttpRequest, full: str, partial: str, context: dict) -> HttpResponse:
    """Full page for a navigation, just the swappable block for htmx."""
    response = render(request, partial if is_htmx(request) else full, context)
    patch_vary_headers(response, ["HX-Request"])
    return response


@login_required
def player(request: HttpRequest, player_id: int) -> HttpResponse:
    subject = get_object_or_404(Player, pk=player_id)
    tab = request.GET.get("aba", DEFAULT_TAB)
    if tab not in TABS:
        raise Http404
    season = _season_filter(request.GET.get("temporada", OVERALL))
    profile = profiles.load(subject)
    is_self = subject.pk == request.user.pk
    context = {
        "subject": subject,
        "profile": profile,
        "tab": tab,
        "tabs": TABS.items(),
        "is_self": is_self,
        "is_board": request.user.is_board,
        "seasons": reversed(seasons.all_seasons()),
        "season_param": OVERALL if season is None else season.number,
    }
    if tab == "resumo":
        context["summary"] = profile.summary(season)
        if is_self or request.user.is_board:
            context["private"] = profiles.private_summary(subject)
    elif tab == "origem":
        context["origin"] = profile.origin()
        if is_self:
            context["invite_link"] = invite_link(request, subject.name)
    return _render_variant(request, "community/player.html", "community/player/_tabs.html", context)


@login_required
def players(request: HttpRequest) -> HttpResponse:
    kind = request.GET.get("tipo", profiles.DEFAULT_KIND)
    if kind not in profiles.KINDS:
        raise Http404
    query = request.GET.get("q", "").strip()
    context = {
        "rows": profiles.directory(query, kind),
        "kinds": profiles.KINDS.items(),
        "kind": kind,
        "query": query,
    }
    return _render_variant(request, "community/players.html", "community/_people.html", context)


@board_required
@require_http_methods(["GET"])
def season(request: HttpRequest) -> HttpResponse:
    """The diretoria's Temporada: the season so far, its end and the seasons already closed."""
    current = seasons.current_season()
    context = {
        "season": current,
        "table": selectors.season_table(current, DEFAULT_METRIC),
        "finished": Season.objects.filter(ends_on__isnull=False).order_by("-number"),
        "voting": selectors.open_votes_in(current),
    }
    return render(request, "community/season.html", context)


@presidente_required
@require_POST
def end_season(request: HttpRequest) -> HttpResponse:
    try:
        ended = services.end_season(request.user)
    except services.CommunityError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, f"Temporada encerrada. Começou a temporada {ended.number}.")
    return redirect("community:season")
