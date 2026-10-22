from datetime import date, timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_http_methods, require_POST

from accounts import throttle
from accounts.models import Player
from accounts.passwords import generate_provisional_password
from board import sunday, tiles
from board.forms import (
    AddToListForm,
    CancelForm,
    ClubSettingsForm,
    ForgiveForm,
    NewPlayerForm,
    PlayerForm,
)
from board.middleware import SundayUnavailable
from board.player_changes import Snapshot, sunday_warnings
from board.player_filters import PlayerFilterForm
from community.selectors import open_voting_day
from core import clock, navigation
from core.decorators import board_required
from core.htmx import is_htmx
from core.models import ZERO, ClubSettings
from core.templatetags.money import brl
from finance import charges, monthly
from finance.models import OPEN_STATUSES, Charge
from finance.selectors import late_mensalistas, payment_history, review_queue
from fines import services as fines
from fines.models import Fine, FineStatus
from games import services
from games.domain.roster import ListError
from games.domain.schedule import SUNDAY, upcoming_sunday
from games.messages import for_board
from games.models import Attendance, GameDay
from games.selectors import BoardRoster, board_roster
from matchday.grouping import first_timers

WEEK = timedelta(days=7)
WEEKS_AHEAD = 4
NEW_LOGIN_SESSION_KEY = "board_new_login"
NEW_PASSWORD_SESSION_KEY = "board_new_password"


@board_required
def dashboard(request: HttpRequest) -> HttpResponse:
    game_day = services.matchday_game_day()
    today = clock.now().date()
    charges.ensure_monthly_charges(today)
    charges.settle_finished_games()
    club = ClubSettings.load()
    pending_fines = Fine.objects.filter(status=FineStatus.PENDING).count()
    receipts_to_review = review_queue().count()
    open_summary = Charge.objects.filter(status__in=OPEN_STATUSES).aggregate(
        count=Count("pk"), total=Sum("amount")
    )
    context = {
        "game_day": game_day,
        "settings": club,
        "late_mensalistas": late_mensalistas(today),
        "groups": tiles.build(
            game_day=game_day,
            roster=board_roster(game_day),
            club=club,
            receipts_to_review=receipts_to_review,
            open_charges=open_summary["count"],
            open_total=open_summary["total"] or ZERO,
            pending_fines=pending_fines,
            gold_ball_open=open_voting_day() is not None,
        ),
    }
    return render(request, "board/dashboard.html", context)


@board_required
def tutorial(request: HttpRequest) -> HttpResponse:
    return render(request, "board/tutorial.html")


@board_required
def players(request: HttpRequest) -> HttpResponse:
    filters = PlayerFilterForm(request.GET)
    everyone = Player.objects.select_related("referred_by").with_enrollment()
    shown = filters.apply(everyone)
    context = {
        "players": shown,
        "count": len(shown),
        "total": everyone.filter(is_active="inativos" not in filters.values()).count(),
        "filters": filters,
        "active": filters.values(),
        "querystring": filters.querystring(),
        "new_login": request.session.pop(NEW_LOGIN_SESSION_KEY, None),
    }
    response = render(
        request, "board/_players_list.html" if is_htmx(request) else "board/players.html", context
    )
    patch_vary_headers(response, ["HX-Request"])
    if context["new_login"]:
        response["Cache-Control"] = "private, no-store"
    return response


@board_required
@require_http_methods(["GET", "POST"])
def player_new(request: HttpRequest) -> HttpResponse:
    """Register a player without putting them on any Sunday's list."""
    form = NewPlayerForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        password = generate_provisional_password()
        try:
            with transaction.atomic():
                player = Player.objects.create_player(
                    password=password,
                    password_is_provisional=True,
                    referred_by=request.user,
                    **form.player_data(),
                )
        except ValidationError:
            form.add_error(None, "Não foi possível criar o jogador. Confira os dados.")
        else:
            if not form.apply_photo(player):
                messages.warning(
                    request, "A foto não foi salva. Envie de novo ao editar o jogador."
                )
            _apply_monthly(request, form, player)
            # Shown once on the next page load, never stored beyond that.
            request.session[NEW_LOGIN_SESSION_KEY] = {
                "name": player.name,
                "phone": player.phone,
                "email": player.email,
                "password": password,
            }
            messages.success(request, f"{player.name} cadastrado.")
            return redirect("board:players")
    return render(
        request, "board/player_new.html", {"form": form, "back": reverse("board:players")}
    )


def _apply_monthly(request: HttpRequest, form: forms.Form, player: Player) -> None:
    """Join the player in this month's plan when requested."""
    wanted = form.cleaned_data["mensalista"]
    if wanted == player.is_mensalista:
        return
    try:
        monthly.join(player, actor=request.user)
    except monthly.MonthlyError as error:
        messages.warning(request, error.message)


def _players_url(filters: PlayerFilterForm) -> str:
    """The player list as the diretoria left it, so editing does not lose the filters."""
    url = reverse("board:players")
    querystring = filters.querystring()
    return f"{url}?{querystring}" if querystring else url


@board_required
@require_http_methods(["GET", "POST"])
def player_edit(request: HttpRequest, player_id: int) -> HttpResponse:
    player = get_object_or_404(Player, pk=player_id)
    before = Snapshot.of(player)
    form = PlayerForm(
        request.POST or None, request.FILES or None, instance=player, editor=request.user
    )
    filters = PlayerFilterForm(request.GET)
    if request.method == "POST" and form.is_valid():
        form.save()
        if not form.apply_photo(player):
            messages.warning(request, "A foto não foi salva. Tente enviar de novo.")
        _apply_monthly(request, form, player)
        messages.success(request, f"{player.name} atualizado.")
        for warning in sunday_warnings(before, player):
            messages.warning(request, warning)
        return redirect(navigation.requested_next(request) or _players_url(filters))
    new_password = request.session.get(NEW_PASSWORD_SESSION_KEY)
    if new_password and new_password["player_id"] == player.pk:
        del request.session[NEW_PASSWORD_SESSION_KEY]
    else:
        new_password = None
    context = {
        "player": player,
        "form": form,
        "back": navigation.requested_next(request) or _players_url(filters),
        "query": request.GET.urlencode(),
        "new_password": new_password,
        "can_reset_password": not player.is_presidente or request.user.is_presidente,
        "payment_history": payment_history(player),
    }
    response = render(request, "board/player_edit.html", context)
    if new_password:
        response["Cache-Control"] = "private, no-store"
    return response


@board_required
@require_POST
def reset_player_password(request: HttpRequest, player_id: int) -> HttpResponse:
    """A player without an e-mail cannot use the recovery link: the diretoria sets a provisional
    password, shown once, and passes it on outside the app (WhatsApp)."""
    player = get_object_or_404(Player, pk=player_id)
    if player.is_presidente and not request.user.is_presidente:
        raise PermissionDenied
    password = generate_provisional_password()
    player.set_password(password)
    player.password_is_provisional = True
    player.save(update_fields=["password", "password_is_provisional"])
    throttle.release_login(player.pk)  # a player locked out by wrong guesses gets in again
    if player.pk == request.user.pk:
        update_session_auth_hash(request, player)  # do not sign the board out of its own screen
    # Shown once on the next page load, never stored beyond that.
    request.session[NEW_PASSWORD_SESSION_KEY] = {
        "player_id": player.pk,
        "phone": player.phone,
        "email": player.email,
        "password": password,
    }
    query = request.GET.urlencode()
    url = reverse("board:player_edit", kwargs={"player_id": player.pk})
    return redirect(f"{url}?{query}" if query else url)


def _reachable(sunday: date) -> bool:
    """A Sunday the diretoria may open: one that exists, or a recent or coming one that can be
    created. Anything else (a typo in the address, a stray arrow) must not create a game day."""
    today = upcoming_sunday(clock.now())
    if today - WEEK <= sunday <= today + WEEKS_AHEAD * WEEK:
        return True
    return GameDay.objects.filter(date=sunday).exists()


def _sunday_or_404(day: str | None) -> GameDay:
    if day is None:
        return services.matchday_game_day()
    try:
        sunday = date.fromisoformat(day)
    except ValueError as exc:
        raise SundayUnavailable from exc
    if sunday.weekday() != SUNDAY or not _reachable(sunday):
        raise SundayUnavailable
    return services.game_day_for(sunday)


def _back(game_day: GameDay) -> HttpResponse:
    return redirect("board:game_day", day=game_day.date.isoformat())


def _active_player_ids(roster: BoardRoster) -> set[int]:
    return {
        row.player_id
        for row in (
            *roster.line,
            *roster.goalkeepers,
            *roster.waiting_line,
            *roster.waiting_goalkeepers,
        )
    }


def _roster_context(current: GameDay) -> dict:
    """What the swappable roster block of the Sunday screen needs."""
    roster = board_roster(current)
    can_mark_attendance = services.game_started(current) and not current.is_cancelled
    return {
        "game_day": current,
        "schedule": services.schedule_for(current.date),
        "roster": roster,
        "add_form": AddToListForm(exclude_ids=_active_player_ids(roster)),
        "can_mark_attendance": can_mark_attendance,
        "cancel_form": CancelForm(),
        "next_fines": _next_fines(roster) if can_mark_attendance else {},
        **sunday.steps_context(current, roster, can_mark_attendance),
        "rookie_ids": first_timers(current, _active_player_ids(roster)),
    }


def _next_fines(roster: BoardRoster) -> dict[int, fines.NextFine]:
    """The fine each player on the list would get for a no-show, to say so before marking it."""
    return fines.next_fines([row.player for row in (*roster.line, *roster.goalkeepers)])


def _roster_changed(request: HttpRequest, current: GameDay) -> HttpResponse:
    """Answer a roster action: swap the roster block for htmx, redirect for a plain form."""
    if is_htmx(request):
        context = {**_roster_context(current), "oob_messages": True}
        return render(request, "board/_roster_board.html", context)
    return _back(current)


def _neighbour(sunday: date) -> str | None:
    """The address of an arrow's Sunday, or None when the arrow would lead nowhere."""
    return sunday.isoformat() if _reachable(sunday) else None


@board_required
def game_day(request: HttpRequest, day: str | None = None) -> HttpResponse:
    current = _sunday_or_404(day)
    context = {
        **_roster_context(current),
        "previous_sunday": _neighbour(current.date - WEEK),
        "next_sunday": _neighbour(current.date + WEEK),
        "attendance": Attendance,
    }
    return render(request, "board/game_day.html", context)


@board_required
@require_POST
def add_to_list(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    form = AddToListForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Escolha um jogador.")
        return _roster_changed(request, current)
    player = form.cleaned_data["player"]
    try:
        result = services.join_list(current.pk, player, board_actor=request.user)
    except ListError as error:
        messages.error(request, error.message)
    else:
        for level, text in for_board(result, player.name):
            messages.add_message(request, level, text)
    return _roster_changed(request, current)


@board_required
@require_POST
def remove_from_list(request: HttpRequest, day: str, player_id: int) -> HttpResponse:
    current = _sunday_or_404(day)
    player = get_object_or_404(Player, pk=player_id)
    try:
        result = services.leave_list(current.pk, player, board_actor=request.user)
    except ListError as error:
        messages.error(request, error.message)
    else:
        for level, text in for_board(result, player.name):
            messages.add_message(request, level, text)
    return _roster_changed(request, current)


@board_required
@require_POST
def mark_attendance(request: HttpRequest, day: str, player_id: int) -> HttpResponse:
    current = _sunday_or_404(day)
    player = get_object_or_404(Player, pk=player_id)
    try:
        fine = services.mark_attendance(
            current.pk, player, request.POST.get("attendance", ""), request.user
        )
    except ListError as error:
        messages.error(request, error.message)
    else:
        if fine is not None:
            messages.warning(request, f"Falta registrada: multa para {player.name}.")
    return _roster_changed(request, current)


@board_required
@require_POST
def mark_late(request: HttpRequest, day: str, player_id: int) -> HttpResponse:
    current = _sunday_or_404(day)
    player = get_object_or_404(Player, pk=player_id)
    try:
        minutes = int(request.POST.get("minutes", "0")) or None
        services.mark_late(current.pk, player, minutes, request.user)
    except (ValueError, ListError) as error:
        messages.error(request, getattr(error, "message", "Valor de atraso inválido."))
    return _roster_changed(request, current)


@board_required
@require_POST
def fine_late(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    try:
        recorded = services.fine_late_arrivals(current.pk, request.user)
    except ListError as error:
        messages.error(request, error.message)
    else:
        if recorded:
            total = sum((fine.amount for fine in recorded), ZERO)
            who = (
                "1 jogador multado" if len(recorded) == 1 else f"{len(recorded)} jogadores multados"
            )
            messages.warning(request, f"{who} por atraso: {brl(total)}.")
        else:
            messages.info(request, "Ninguém marcado com atraso ficou sem multa.")
    return _roster_changed(request, current)


@board_required
@require_POST
def mark_all_present(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    try:
        marked = services.mark_all_present(current.pk, request.user)
    except ListError as error:
        messages.error(request, error.message)
    else:
        if marked:
            who = (
                "1 jogador marcado como presente"
                if marked == 1
                else f"{marked} jogadores marcados como presentes"
            )
            messages.success(request, f"{who}.")
        else:
            messages.info(request, "Todos da lista já estão com a presença marcada.")
    return _roster_changed(request, current)


@board_required
@require_POST
def cancel_game_day(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    form = CancelForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Informe o motivo do cancelamento.")
        return _back(current)
    try:
        voided = services.cancel_game_day(current.pk, request.user, form.cleaned_data["reason"])
    except ListError as error:
        messages.error(request, error.message)
    else:
        suffix = f" {voided} multa(s) deste domingo cancelada(s)." if voided else ""
        messages.success(request, f"Domingo cancelado.{suffix}")
    return _back(current)


@board_required
@require_POST
def finish_game_day(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    try:
        opened = services.finish_game_day(current.pk, request.user)
    except ListError as error:
        messages.error(request, error.message)
    else:
        if opened:
            messages.success(request, "Jogo finalizado. As cobranças do domingo foram abertas.")
        else:
            messages.info(request, "Este jogo já estava finalizado.")
    return _back(current)


@board_required
@require_POST
def reopen_game_day(request: HttpRequest, day: str) -> HttpResponse:
    current = _sunday_or_404(day)
    try:
        services.reopen_game_day(current.pk, request.user)
    except ListError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, "Domingo reaberto.")
    return _back(current)


@board_required
def fine_list(request: HttpRequest) -> HttpResponse:
    related = ("player", "game_day", "resolved_by", "charge")
    context = {
        "pending": Fine.objects.filter(status=FineStatus.PENDING).select_related(*related),
        "resolved": Fine.objects.exclude(status=FineStatus.PENDING).select_related(*related)[:30],
    }
    return render(request, "board/fines.html", context)


@board_required
@require_http_methods(["GET", "POST"])
def forgive_fine(request: HttpRequest, fine_id: int) -> HttpResponse:
    fine = get_object_or_404(Fine.objects.select_related("player", "game_day"), pk=fine_id)
    form = ForgiveForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            fines.forgive(fine.pk, request.user, form.cleaned_data["note"])
        except fines.FineError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, f"Multa de {fine.player.name} perdoada.")
        return redirect("board:fines")
    return render(request, "board/forgive.html", {"fine": fine, "form": form})


@board_required
@require_http_methods(["GET", "POST"])
def club_settings(request: HttpRequest) -> HttpResponse:
    current = ClubSettings.load()
    form = ClubSettingsForm(request.POST or None, instance=current, editor=request.user)
    if request.method == "POST" and form.is_valid():
        updated = form.save(commit=False)
        updated.updated_by = request.user
        if "pix_key" in form.changed_data:
            updated.pix_key_changed_by = request.user
            updated.pix_key_changed_at = clock.now()
        updated.save()
        messages.success(request, "Valores salvos.")
        return redirect("board:settings")
    return render(request, "board/settings.html", {"form": form, "settings": current})
