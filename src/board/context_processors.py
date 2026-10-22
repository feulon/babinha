from django.http import HttpRequest
from django.urls import reverse
from django.utils.functional import SimpleLazyObject

from core import clock
from games.domain.schedule import matchday_sunday
from matchday.models import Match


def _entry_url() -> str:
    """The match on court while one is being played, so scoring is one tap away; else the
    Painel."""
    playing = (
        Match.objects.filter(
            lineup__game_day__date=matchday_sunday(clock.now()), finished_at__isnull=True
        )
        .order_by("order")
        .values_list("pk", flat=True)
        .first()
    )
    if playing is None:
        return reverse("board:dashboard")
    return reverse("board:match", kwargs={"match_id": playing})


def board_entry(request: HttpRequest) -> dict[str, object]:
    """`board_entry_url` is where the Diretoria tab opens. Lazy: one query, and only on the
    screens of a diretoria member, the only ones that show the tab."""
    user = request.user
    if not (user.is_authenticated and user.is_board):
        return {"board_entry_url": ""}
    return {"board_entry_url": SimpleLazyObject(_entry_url)}
