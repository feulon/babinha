"""The monthly plan: a player joins for one calendar month and starts over on the 1st.

Joining is a button (the player's, or the diretoria's on their behalf) that stores an
`accounts.MonthlyEnrollment` for the current month and opens that month's fee as a charge. The
charge is paid whenever the player sends the Pix receipt and the diretoria confirms it; until
then the enrollment holds and the fee shows up as pending. Nothing renews by itself and no job
resets anything: the month of the clock is the reference, so on the 1st everybody is back to
their standing type until they join again.
"""

from django.db import transaction

from accounts.models import MonthlyEnrollment, Player, month_start
from core import clock
from finance import charges


class MonthlyError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@transaction.atomic
def join(player: Player, *, actor: Player | None = None) -> tuple[MonthlyEnrollment, bool]:
    """Make `player` a mensalista of the current month and open the month's fee.

    `actor` is the diretoria registering someone else. Returns the enrollment and whether it is
    new: joining twice does nothing, so a double click never charges twice.
    """
    player = Player.objects.select_for_update(no_key=True).get(pk=player.pk)
    if not player.is_active:
        raise MonthlyError("Jogador inativo.")
    now = clock.now()
    month = month_start(now.date())
    enrollment, created = MonthlyEnrollment.objects.get_or_create(
        player=player,
        month=month,
        defaults={"enrolled_at": now, "enrolled_by": actor},
    )
    charges.ensure_monthly_charges(month, player=player)
    return enrollment, created
