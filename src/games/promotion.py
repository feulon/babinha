"""An agregado who has played enough futebóis becomes avulso."""

from django.db.models import Count, F, Q

from accounts.models import Player, PlayerType
from core import clock
from core.models import ClubSettings
from games.models import Attendance


def promote_agregados() -> list[Player]:
    """Turn into avulso every agregado with `avulso_after_games` or more futebóis.

    A futebol is a presence marked on a finished Sunday. When the diretoria or an earlier
    promotion set the type, only the Sundays finished after that count, so a type put back to
    agregado by hand stays until new presences add up. Mensalistas are a monthly enrollment, not
    a standing type, and avulsos are never touched, so this only ever moves agregado to avulso
    and running it again changes nothing.
    """
    threshold = ClubSettings.load().avulso_after_games
    counted = Q(
        list_entries__attendance=Attendance.PRESENTE,
        list_entries__game_day__finished_at__isnull=False,
        list_entries__game_day__cancelled_at__isnull=True,
    ) & (
        Q(player_type_set_at__isnull=True)
        | Q(list_entries__game_day__finished_at__gt=F("player_type_set_at"))
    )
    due = list(
        Player.objects.filter(player_type=PlayerType.AGREGADO)
        .annotate(games=Count("list_entries", filter=counted))
        .filter(games__gte=threshold)
    )
    if due:
        now = clock.now()
        Player.objects.filter(pk__in=[p.pk for p in due]).update(
            player_type=PlayerType.AVULSO, player_type_set_at=now
        )
    return due
