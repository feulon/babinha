"""Diretoria screens of the game day: team draw, match table and results."""

from collections.abc import Callable

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import Player
from board.matchday_forms import (
    GoalEditForm,
    GoalForm,
    GoalkeepersForm,
    MatchTeamsForm,
    RosterForm,
)
from board.views import _sunday_or_404
from core.decorators import board_required
from core.htmx import is_htmx
from games import services as games_services
from matchday import services
from matchday.domain.score import MATCH_MINUTES, Side
from matchday.models import Goal, Match, Team
from matchday.selectors import match_row, match_rows
from matchday.services import MatchdayError


def _to_match(match_id: int) -> HttpResponse:
    return redirect("board:match", match_id=match_id)


def _attempt[T](
    request: HttpRequest, action: Callable[[], T], success: str | Callable[[T], str]
) -> bool:
    """Run a service call, flashing its error or `success` (a text, or one made from the result)."""
    try:
        result = action()
    except MatchdayError as error:
        messages.error(request, error.message)
        return False
    messages.success(request, success if isinstance(success, str) else success(result))
    return True


def _first_error(form: object) -> str:
    errors = [message for field_errors in form.errors.values() for message in field_errors]
    return errors[0] if errors else "Confira os campos."


# Match table


@board_required
@require_http_methods(["GET"])
def matches(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    lineup = services.lineup_for(current)
    context: dict = {"game_day": current, "lineup": lineup}
    if lineup is not None:
        rows = match_rows(lineup)
        context |= {
            "rows": rows,
            "playing": next((row for row in rows if not row.match.is_finished), None),
            "next_up": services.next_up(lineup),
        }
    return render(request, "board/matches.html", context)


@board_required
@require_POST
def start_next_match(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    try:
        started = services.start_next_match(current.pk, request.user)
    except MatchdayError as error:
        messages.error(request, error.message)
        return redirect("board:matches", day=current.date.isoformat())
    messages.success(request, f"{started.home_team.name} x {started.away_team.name} em campo.")
    return _to_match(started.pk)


# One match


def _match_or_404(match_id: int) -> Match:
    return get_object_or_404(
        Match.objects.select_related(
            "lineup__game_day", "home_team", "away_team", "home_goalkeeper", "away_goalkeeper"
        ),
        pk=match_id,
    )


def _side_or_404(side: str) -> Side:
    try:
        return Side(side)
    except ValueError as exc:
        raise Http404 from exc


def _goal_form(match: Match, side: Side, data: object = None) -> GoalForm:
    opponent = match.away_team if side == Side.HOME else match.home_team
    return GoalForm(
        data,
        auto_id=f"id_{side.value}_%s",
        players=services.side_players(match, side),
        opponent=f"do {opponent.name}",
    )


def _timer_running(current: Match, row) -> bool:
    """The match is on court: the Sunday started and the match is neither ended nor won."""
    started = services.has_started(current.lineup.game_day)
    return started and not current.is_finished and not row.score.decided


def _timer_on(current: Match) -> bool:
    return _timer_running(current, match_row(current))


def _next_up_after(current: Match) -> services.NextUp | None:
    """The match the waiting line calls once this one is over, if it is the last of the table."""
    if not current.is_finished:
        return None
    if Match.objects.filter(lineup_id=current.lineup_id, order__gt=current.order).exists():
        return None
    return services.next_up(current.lineup)


def _assisters(current: Match, side: Side, goalkeeper: Player | None) -> list[Player]:
    """Who can be asked about as the assist of a goal for `side`: its players, then its keeper."""
    players = services.side_scorers(current, side)
    return [*players, goalkeeper] if goalkeeper and goalkeeper not in players else players


def _live_context(current: Match, asking: tuple[Side, Player] | None = None) -> dict:
    """The part of the match screen htmx refreshes: score, goal forms, goals, finish button.

    With `asking` (the side and the scorer just tapped) that side shows the assist question
    instead of its scorer buttons."""
    row = match_row(current)
    sides = [
        {
            "side": side.value,
            "team": team,
            "goalkeeper": goalkeeper,
            "goals": row.score.home if side == Side.HOME else row.score.away,
            "form": _goal_form(current, side),
            "scorers": services.side_scorers(current, side),
            "assisters": _assisters(current, side, goalkeeper),
            "asking": asking[1] if asking and asking[0] == side else None,
        }
        for side, team, goalkeeper in (
            (Side.HOME, current.home_team, current.home_goalkeeper),
            (Side.AWAY, current.away_team, current.away_goalkeeper),
        )
    ]
    return {
        "game_day": current.lineup.game_day,
        "match": current,
        "row": row,
        "sides": sides,
        "next_up": _next_up_after(current),
        # Goals can be added at any time, even once the match ended, until a team has won it,
        # but only once the diretoria started the Sunday.
        "started": services.has_started(current.lineup.game_day),
        "can_start": games_services.can_start(current.lineup.game_day),
        "can_score": not row.score.decided,
        "timer_on": _timer_running(current, row),
    }


def _page_context(current: Match, asking: tuple[Side, Player] | None = None) -> dict:
    goalkeepers_form = GoalkeepersForm(
        pool=services.match_goalkeeper_pool(current),
        initial={
            "home_goalkeeper": current.home_goalkeeper_id or "",
            "away_goalkeeper": current.away_goalkeeper_id or "",
        },
    )
    return {
        **_live_context(current, asking),
        "goalkeepers_form": goalkeepers_form,
        "roster_form": _roster_form(current),
        "roster": services.match_roster(current),
        "teams_form": MatchTeamsForm(
            teams=list(current.lineup.teams.all()),
            initial={
                "home_team": str(current.home_team.number),
                "away_team": str(current.away_team.number),
            },
        ),
        "match_minutes": MATCH_MINUTES,
        "match_seconds": MATCH_MINUTES * 60,
    }


@board_required
def match(request: HttpRequest, match_id: int) -> HttpResponse:
    current = _match_or_404(match_id)
    return render(request, "board/match.html", _page_context(current))


@board_required
@require_http_methods(["GET"])
def ask_assist(request: HttpRequest, match_id: int, side: str) -> HttpResponse:
    """After the scorer is tapped: ask who gave the assist (a player, nobody or not applicable)."""
    current = _match_or_404(match_id)
    chosen = _side_or_404(side)
    scorer = next(
        (
            player
            for player in services.side_scorers(current, chosen)
            if str(player.pk) == request.GET.get("scorer")
        ),
        None,
    )
    if scorer is None or not services.has_started(current.lineup.game_day):
        return _to_match(current.pk)
    asking = (chosen, scorer)
    if is_htmx(request):
        return render(request, "board/_match_live.html", _live_context(current, asking))
    return render(request, "board/match.html", _page_context(current, asking))


def _goals_changed(request: HttpRequest, match_id: int, *, timer_was_on: bool) -> HttpResponse:
    """Answer a goal action: refresh the live block for htmx, redirect for a plain form.

    The timer sits outside the live block, so when the goal started or stopped it (a team just
    won, or a corrected score reopened the match) htmx does a full reload instead.
    """
    if not is_htmx(request):
        return _to_match(match_id)
    context = _live_context(_match_or_404(match_id))
    if context["timer_on"] != timer_was_on:
        response = HttpResponse(status=204)
        response["HX-Redirect"] = reverse("board:match", kwargs={"match_id": match_id})
        return response
    return render(request, "board/_match_live.html", {**context, "oob_messages": True})


def _roster_form(match: Match, data: object = None) -> RosterForm:
    current = {
        row.player_id: Side.HOME if row.team_id == match.home_team_id else Side.AWAY
        for row in match.players.all()
    }
    return RosterForm(
        data,
        players=services.roster_candidates(match),
        home=match.home_team,
        away=match.away_team,
        current=current,
    )


@board_required
@require_POST
def set_roster(request: HttpRequest, match_id: int) -> HttpResponse:
    current = _match_or_404(match_id)
    form = _roster_form(current, request.POST)
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return _to_match(current.pk)
    _attempt(
        request,
        lambda: services.set_roster(current.pk, request.user, form.assignments()),
        "Escalação da partida salva.",
    )
    return _to_match(current.pk)


@board_required
@require_POST
def edit_match(request: HttpRequest, match_id: int) -> HttpResponse:
    current = _match_or_404(match_id)
    form = MatchTeamsForm(request.POST, teams=list(current.lineup.teams.all()))
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return _to_match(current.pk)
    home, away = form.teams()
    _attempt(
        request,
        lambda: services.edit_match(current.pk, request.user, home_team=home, away_team=away),
        "Times da partida salvos.",
    )
    return _to_match(current.pk)


def _move_match(request: HttpRequest, match_id: int, *, earlier: bool) -> HttpResponse:
    current = _match_or_404(match_id)
    _attempt(
        request,
        lambda: services.move_match(current.pk, request.user, earlier=earlier),
        "Partida movida na tabela.",
    )
    return redirect("board:matches", day=current.lineup.game_day.date.isoformat())


@board_required
@require_POST
def move_match_up(request: HttpRequest, match_id: int) -> HttpResponse:
    return _move_match(request, match_id, earlier=True)


@board_required
@require_POST
def move_match_down(request: HttpRequest, match_id: int) -> HttpResponse:
    return _move_match(request, match_id, earlier=False)


@board_required
@require_POST
def set_goalkeepers(request: HttpRequest, match_id: int) -> HttpResponse:
    current = _match_or_404(match_id)
    form = GoalkeepersForm(request.POST, pool=services.match_goalkeeper_pool(current))
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return _to_match(current.pk)
    home, away = form.goalkeepers()
    _attempt(
        request,
        lambda: services.set_goalkeepers(current.pk, request.user, home=home, away=away),
        "Goleiros salvos.",
    )
    return _to_match(current.pk)


def _goal_message(current: Match, team: Team) -> str:
    """The flash after a goal; the one that decides the match says so, with the final score."""
    score = services.score_of(current)
    if score.decided:
        return f"Gol do {team.name}! Fim de jogo: {score.home} x {score.away}."
    return f"Gol do {team.name}!"


@board_required
@require_POST
def add_goal(request: HttpRequest, match_id: int, side: str) -> HttpResponse:
    current = _match_or_404(match_id)
    chosen = _side_or_404(side)
    form = _goal_form(current, chosen, request.POST)
    timer_was_on = _timer_on(current)
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return _goals_changed(request, current.pk, timer_was_on=timer_was_on)
    team = current.home_team if chosen == Side.HOME else current.away_team
    _attempt(
        request,
        lambda: services.add_goal(
            current.pk,
            request.user,
            side=chosen,
            scorer=form.scorer_id(),
            assist=form.assist_id(),
            assist_not_applicable=form.assist_not_applicable,
            own_goal=form.own_goal,
        ),
        lambda _: _goal_message(current, team),
    )
    return _goals_changed(request, current.pk, timer_was_on=timer_was_on)


@board_required
@require_http_methods(["GET", "POST"])
def edit_goal(request: HttpRequest, match_id: int, goal_id: int) -> HttpResponse:
    goal = get_object_or_404(
        Goal.objects.select_related("scorer", "assist"), pk=goal_id, match_id=match_id
    )
    current = _match_or_404(match_id)
    players = services.side_players(current, Side.HOME)
    players += [p for p in (goal.scorer, goal.assist) if p is not None and p not in players]
    form = GoalEditForm(
        request.POST or None,
        players=players,
        home=current.home_team,
        away=current.away_team,
        goal=goal,
    )
    if request.method == "POST":
        if form.is_valid():
            saved = _attempt(
                request,
                lambda: services.edit_goal(
                    goal.pk,
                    request.user,
                    side=form.side_value(),
                    scorer=form.scorer_id(),
                    assist=form.assist_id(),
                    assist_not_applicable=form.assist_not_applicable,
                    own_goal=form.own_goal,
                ),
                "Gol corrigido.",
            )
            if saved:
                return _to_match(current.pk)
        else:
            messages.error(request, _first_error(form))
    context = {"game_day": current.lineup.game_day, "match": current, "goal": goal, "form": form}
    return render(request, "board/goal_edit.html", context)


@board_required
@require_POST
def delete_goal(request: HttpRequest, match_id: int, goal_id: int) -> HttpResponse:
    goal = get_object_or_404(Goal, pk=goal_id, match_id=match_id)
    timer_was_on = _timer_on(_match_or_404(match_id))
    _attempt(
        request,
        lambda: services.delete_goal(goal.pk, request.user),
        lambda reopened: (
            "Gol apagado. A partida voltou para Em andamento." if reopened else "Gol apagado."
        ),
    )
    return _goals_changed(request, match_id, timer_was_on=timer_was_on)


@board_required
@require_POST
def finish_match(request: HttpRequest, match_id: int) -> HttpResponse:
    current = _match_or_404(match_id)
    _attempt(request, lambda: services.finish_match(current.pk, request.user), "Partida encerrada.")
    return _to_match(current.pk)


@board_required
@require_POST
def reopen_match(request: HttpRequest, match_id: int) -> HttpResponse:
    current = _match_or_404(match_id)
    _attempt(request, lambda: services.reopen_match(current.pk, request.user), "Partida reaberta.")
    return _to_match(current.pk)


@board_required
@require_POST
def delete_match(request: HttpRequest, match_id: int) -> HttpResponse:
    current = _match_or_404(match_id)
    deleted = _attempt(
        request, lambda: services.delete_match(current.pk, request.user), "Partida apagada."
    )
    if deleted:
        return redirect("board:matches", day=current.lineup.game_day.date.isoformat())
    return _to_match(current.pk)
