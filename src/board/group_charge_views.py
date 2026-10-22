"""Diretoria screen that builds the group message charging everyone who owes."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from board.reminder_views import _debtors
from core.decorators import board_required
from finance import group_charge
from finance.selectors import pix_info


@board_required
def group_charge_view(request: HttpRequest) -> HttpResponse:
    debtors = _debtors()
    message = group_charge.compose(debtors, pix_info())
    context = {
        "message": message,
        "whatsapp_url": group_charge.whatsapp_share_url(message) if message else "",
        "without_phone": [
            d.player.name for d in debtors if not group_charge.mention(d.player.phone)
        ],
    }
    return render(request, "board/group_charge.html", context)
