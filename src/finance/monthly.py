"""The monthly plan: a player joins for the Sundays of one month and starts over after the last.

Joining is a button (the player's, or the diretoria's on their behalf) that stores an
`accounts.MonthlyEnrollment` for the plan month and opens that month's fee as a charge. The plan
month is the month of the next Sunday that has not ended (`accounts.models.plan_month`): the
calendar month, except that once the month's last Sunday has ended joining is already for the
next month, so whoever joins makes it in time for the list of its first Sunday. The charge is
paid whenever the player sends the Pix receipt and the diretoria confirms it, even before the
month starts; until then the enrollment holds and the fee shows up as pending. Nothing renews by
itself and no job resets anything: after the month's last Sunday everybody is back to their
standing type until they join again.
"""

from django.db import transaction

from accounts.models import MonthlyEnrollment, Player, plan_month
from core import clock
from finance import charges


class MonthlyError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@transaction.atomic
def join(player: Player, *, actor: Player | None = None) -> tuple[MonthlyEnrollment, bool]:
    """Make `player` a mensalista of the plan month and open the month's fee.

    `actor` is the diretoria registering someone else. Returns the enrollment and whether it is
    new: joining twice does nothing, so a double click never charges twice.
    """
    player = Player.objects.select_for_update(no_key=True).get(pk=player.pk)
    if not player.is_active:
        raise MonthlyError("Jogador inativo.")
    now = clock.now()
    month = plan_month(now.date())
    enrollment, created = MonthlyEnrollment.objects.get_or_create(
        player=player,
        month=month,
        defaults={"enrolled_at": now, "enrolled_by": actor},
    )
    charges.ensure_monthly_charges(month, player=player)
    return enrollment, created
