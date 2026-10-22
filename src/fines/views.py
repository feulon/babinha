from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from fines.models import Fine


@login_required
def my_fines(request: HttpRequest) -> HttpResponse:
    fines = Fine.objects.filter(player=request.user).select_related("game_day", "resolved_by")
    return render(request, "fines/my_fines.html", {"fines": fines})
