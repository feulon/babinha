"""Diretoria screen to e-mail a pending-payment notice to the players who owe."""

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from core import clock
from core.decorators import board_required
from finance import charges, reminders
from finance.reminders import Debtor
from finance.selectors import open_charges, pix_info


def _debtors() -> tuple[Debtor, ...]:
    today = clock.now().date()
    charges.ensure_monthly_charges(today)
    return reminders.debtors(open_charges(today))


def _selected(request: HttpRequest, everyone: tuple[Debtor, ...]) -> tuple[Debtor, ...]:
    ids = set(request.POST.getlist("player"))
    return tuple(d for d in everyone if str(d.player.pk) in ids)


def _app_url(request: HttpRequest) -> str:
    return request.build_absolute_uri(reverse("finance:payments"))


def _list_page(request: HttpRequest, everyone: tuple[Debtor, ...]) -> HttpResponse:
    context = {
        "debtors": everyone,
        "pix": pix_info(),
        "window_days": reminders.RECENT_WINDOW.days,
    }
    return render(request, "board/reminders.html", context)


def _preview_page(
    request: HttpRequest, chosen: tuple[Debtor, ...], everyone: tuple[Debtor, ...]
) -> HttpResponse:
    if not chosen:
        messages.error(request, "Escolha pelo menos um jogador para avisar.")
        return _list_page(request, everyone)
    example = next((d for d in chosen if d.has_email), chosen[0])
    context = {
        "chosen": chosen,
        "to_send": sum(1 for d in chosen if d.has_email and not d.recently_reminded),
        "without_email": [d for d in chosen if not d.has_email],
        "recent": [d for d in chosen if d.recently_reminded],
        "example": example,
        "subject": reminders.SUBJECT,
        "body": reminders.compose(example, _app_url(request)),
        "window_days": reminders.RECENT_WINDOW.days,
    }
    return render(request, "board/reminders_preview.html", context)


@board_required
@require_http_methods(["GET", "POST"])
def reminders_view(request: HttpRequest) -> HttpResponse:
    everyone = _debtors()
    if request.method == "GET":
        return _list_page(request, everyone)
    chosen = _selected(request, everyone)
    if request.POST.get("step") != "send":
        return _preview_page(request, chosen, everyone)
    if not chosen:
        messages.error(request, "Esses jogadores já não têm pendência.")
        return redirect("board:reminders")
    result = reminders.send_reminders(
        chosen, request.user, _app_url(request), repeat=request.POST.get("repeat") == "1"
    )
    return render(request, "board/reminders_result.html", {"result": result})
