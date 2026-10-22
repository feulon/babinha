"""Diretoria screen to assemble the Sunday's teams, all on one page: draw, adjust and confirm.

One page, `board:teams`; picking someone to move is `?unidade=`. Every action answers an HTMX
request by swapping the whole `#times` block and a plain form post by redirecting to the page, so
everything works without JavaScript too.
"""

from collections.abc import Callable

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_POST

from accounts.models import Player
from board.matchday_forms import TeamNamesForm
from board.matchday_views import _first_error
from board.times_forms import GroupForm
from board.views import _sunday_or_404
from core.decorators import board_required
from core.htmx import is_htmx
from games.models import GameDay
from matchday import adjusting, services, team_rules
from matchday.adjusting import GOAL, MOVE, SWAP
from matchday.grouping import Grouping
from matchday.selectors import Teams, lineup_state, teams_of
from matchday.services import MatchdayError, RulesConflict

TEAM_BLOCK = "board/_times.html"


def _url(game_day: GameDay) -> str:
    return reverse("board:teams", kwargs={"day": game_day.date.isoformat()})


def _group_form(grouping: Grouping, data: object = None) -> GroupForm:
    taken = {player.pk for group in grouping.manual for player in group.players}
    return GroupForm(data, candidates=[p for p in grouping.line if p.pk not in taken])


def _context(
    request: HttpRequest,
    game_day: GameDay,
    *,
    picked: str | None = None,
    group_form: GroupForm | None = None,
    names_form: TeamNamesForm | None = None,
) -> dict:
    state = teams_of(game_day)
    grouping = services.grouping_of(game_day)
    context: dict = {
        "game_day": game_day,
        "state": state,
        "grouping": grouping,
        "lineup_state": lineup_state(state),
        "problem": team_rules.rules_problem(grouping),
        "released": [row.player for row in grouping.rookie_rows if row.state == "released"],
        "bound": sum(1 for row in grouping.rookie_rows if row.state == "applies"),
        "group_form": group_form or _group_form(grouping),
    }
    if state.lineup is not None:
        teams = [roster.team for roster in state.teams]
        context |= {
            "board": adjusting.board_for(state, grouping, picked or request.GET.get("unidade")),
            "split": adjusting.split_lines(state, grouping),
            "imbalances": [
                adjusting.describe(i, adjusting.team_names(state))
                for i in adjusting.imbalances_of(state)
            ],
            "names_form": names_form or TeamNamesForm(teams=teams),
        }
    return context


def _render(request: HttpRequest, game_day: GameDay, **extra: object) -> HttpResponse:
    """The page, or just the `#times` block for HTMX (which only swaps a 200)."""
    context = _context(request, game_day, **extra)
    if is_htmx(request):
        response = render(request, TEAM_BLOCK, {**context, "oob_messages": True})
        if request.method == "POST":  # a GET pushes (or not) by its own hx-push-url
            response["HX-Push-Url"] = _url(game_day)
    else:
        response = render(request, "board/teams.html", context)
    patch_vary_headers(response, ["HX-Request"])  # the same address answers the page and the block
    return response


def _answer(request: HttpRequest, game_day: GameDay) -> HttpResponse:
    """After an action: HTMX gets the page right away, a plain form is redirected to it."""
    if is_htmx(request):
        return _render(request, game_day)
    return redirect(_url(game_day))


def _attempt(request: HttpRequest, action: Callable[[], object], success: str) -> bool:
    try:
        action()
    except MatchdayError as error:
        messages.error(request, error.message)
        return False
    messages.success(request, success)
    return True


@board_required
def teams(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    return _render(request, current)


# Rules


@board_required
@require_POST
def rookie_rule(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    enabled = request.POST.get("enabled") == "on"
    _attempt(
        request,
        lambda: team_rules.set_rookie_rule(current.pk, request.user, enabled),
        "Estreantes ficam com quem convidou." if enabled else "Regra dos estreantes desligada.",
    )
    return _answer(request, current)


@board_required
@require_POST
def release_rookie(request: HttpRequest, day: str, player_id: int) -> HttpResponse:
    current = _sunday_or_404(day)
    name = (
        Player.objects.filter(pk=player_id).values_list("name", flat=True).first() or "O estreante"
    )
    _attempt(
        request,
        lambda: team_rules.release_rookie(current.pk, request.user, player_id),
        f"{name} vai poder jogar em outro time, só neste domingo.",
    )
    return _answer(request, current)


@board_required
@require_POST
def restore_rookie(request: HttpRequest, day: str, player_id: int) -> HttpResponse:
    current = _sunday_or_404(day)
    _attempt(
        request,
        lambda: team_rules.restore_rookie(current.pk, request.user, player_id),
        "O estreante voltou a ficar com quem convidou.",
    )
    return _answer(request, current)


@board_required
@require_POST
def create_group(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    grouping = services.grouping_of(current)
    form = _group_form(grouping, request.POST)
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return _render(request, current, group_form=form)
    saved = _attempt(
        request,
        lambda: team_rules.create_group(
            current.pk, request.user, form.cleaned_data["players"], form.cleaned_data["name"]
        ),
        "Grupo salvo.",
    )
    if not saved:
        return _render(request, current, group_form=form)
    return _answer(request, current)


@board_required
@require_POST
def delete_group(request: HttpRequest, day: str, group_id: int) -> HttpResponse:
    current = _sunday_or_404(day)
    _attempt(
        request,
        lambda: team_rules.delete_group(current.pk, request.user, group_id),
        "Grupo desfeito.",
    )
    return _answer(request, current)


# Draw


@board_required
@require_POST
def draw_teams(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    try:
        services.draw(current.pk, request.user)
    except RulesConflict:  # the page says why and offers the ways out
        return _answer(request, current)
    except MatchdayError as error:
        messages.error(request, error.message)
        return _answer(request, current)
    messages.success(request, "Times sorteados.")
    return _answer(request, current)


@board_required
@require_POST
def resolve_conflict(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    choice = request.POST.get("exit", "")
    chosen = team_rules.resolution_for(services.grouping_of(current), choice)
    try:
        team_rules.resolve_conflict(current.pk, request.user, choice)
    except RulesConflict:
        return _answer(request, current)
    except MatchdayError as error:
        messages.error(request, error.message)
        return _answer(request, current)
    changed = f"{chosen.done} " if chosen is not None and chosen.done else ""
    messages.success(request, f"{changed}Times sorteados.")
    return _answer(request, current)


# Adjust and confirm


def _parse_destination(state: Teams, raw: str) -> int | str | None:
    numbers = {roster.team.number for roster in state.teams}
    if raw == GOAL:
        return GOAL
    return int(raw) if raw.isdigit() and int(raw) in numbers else None


@board_required
@require_POST
def adjust_teams(request: HttpRequest, day: str) -> HttpResponse:
    """Move a player or group to a team, or swap it with another: the teams are saved at once."""
    current = _sunday_or_404(day)
    state = teams_of(current)
    if state.lineup is None:
        messages.error(request, "Sorteie os times primeiro.")
        return _answer(request, current)
    grouping = services.grouping_of(current)
    units = {unit.key: unit for unit in adjusting.units_of(state, grouping)}
    unit = units.get(request.POST.get("unidade", ""))
    mode = SWAP if request.POST.get("modo") == SWAP else MOVE
    placement = adjusting.placement_of(state)
    if unit is None:
        messages.error(request, "Escolha quem mover.")
        return _render(request, current, picked=request.POST.get("unidade"))

    if mode == MOVE:
        place = _parse_destination(state, request.POST.get("destino", ""))
        if place is None:
            messages.error(request, "Escolha para qual time mover.")
            return _render(request, current, picked=request.POST.get("unidade"))
        after = adjusting.moved(placement, unit, place)
        if blocked := adjusting.goal_blocked(placement, after):
            messages.error(request, blocked)
            return _render(request, current, picked=request.POST.get("unidade"))
        done = f"{unit.label} {'foi' if len(unit.players) == 1 else 'foram'} para " + (
            "o gol." if place == GOAL else f"o {adjusting.place_label(state, place)}."
        )
    else:
        other = units.get(request.POST.get("destino", ""))
        if other is None or other.place in (adjusting.NEW, adjusting.SPLIT, unit.place):
            messages.error(request, "Escolha com quem trocar.")
            return _render(request, current, picked=request.POST.get("unidade"))
        after = adjusting.swapped(placement, unit, other)
        done = f"{unit.label} e {other.label} trocaram de lugar."

    assignments = {
        pid: None if place == GOAL else place
        for pid, place in after.items()
        if place != adjusting.NEW
    }
    saved = _attempt(
        request,
        lambda: services.assign(current.pk, request.user, assignments, leave_unplaced=True),
        done[0].upper() + done[1:],
    )
    if not saved:
        return _render(request, current, picked=request.POST.get("unidade"))
    return _answer(request, current)


@board_required
@require_POST
def rename_teams(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    state = teams_of(current)
    if state.lineup is None:
        messages.error(request, "Sorteie os times primeiro.")
        return _answer(request, current)
    form = TeamNamesForm(request.POST, teams=[roster.team for roster in state.teams])
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return _render(request, current, names_form=form)
    _attempt(
        request,
        lambda: services.rename_teams(current.pk, request.user, form.names()),
        "Nomes dos times salvos.",
    )
    return _answer(request, current)


@board_required
@require_POST
def confirm_teams(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    _attempt(
        request,
        lambda: services.confirm(current.pk, request.user),
        "Times confirmados: os jogadores já podem ver.",
    )
    return _answer(request, current)


@board_required
@require_POST
def accept_potes(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    _attempt(
        request,
        lambda: services.accept_pote_changes(current.pk, request.user),
        "Times mantidos com os potes novos.",
    )
    return _answer(request, current)


@board_required
@require_POST
def unconfirm_teams(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    _attempt(
        request,
        lambda: services.unconfirm(current.pk, request.user),
        "Confirmação desfeita: os times voltaram a ser rascunho.",
    )
    return _answer(request, current)
