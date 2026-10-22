"""Community operations: casting a vote and ending the season.

Like the list and game day operations, they first take `SELECT ... FOR UPDATE` on a row everyone
shares (the Sunday, or the open season), so two requests never interleave.
"""

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F

from accounts.models import Player
from community import seasons
from community.domain import voting
from community.models import Ballot, PlaceTally, Season
from community.selectors import eligible_voters, window_of
from core import clock
from games.models import GameDay


class CommunityError(Exception):
    """An operation that is not allowed. `message` is shown to the user (pt-BR)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@transaction.atomic
def cast_vote(
    game_day_id: int, voter: Player, *, first: int | None, second: int | None, third: int | None
) -> None:
    """Record the voter's three picks (5, 3 and 2 points). Votes are final and anonymous."""
    game_day = GameDay.objects.select_for_update().get(pk=game_day_id)
    window = window_of(game_day)
    if window.phase == voting.Phase.CLOSED:
        raise CommunityError("A votação deste domingo já fechou.")
    if not window.is_open:
        raise CommunityError("A votação ainda não abriu: ela abre quando o futebol for encerrado.")
    if Ballot.objects.filter(game_day=game_day, voter=voter).exists():
        raise CommunityError("Você já votou neste domingo. O voto não pode ser alterado.")

    try:
        picks = voting.validate_ballot(
            voter=voter.pk,
            picks={1: first, 2: second, 3: third},
            eligible=[player.pk for player in eligible_voters(game_day)],
        )
    except voting.BallotError as error:
        raise CommunityError(error.message) from error

    Ballot.objects.create(game_day=game_day, voter=voter)
    for place, nominee_id in picks.items():
        PlaceTally.objects.get_or_create(game_day=game_day, nominee_id=nominee_id, place=place)
        PlaceTally.objects.filter(game_day=game_day, nominee_id=nominee_id, place=place).update(
            votes=F("votes") + 1
        )


@transaction.atomic
def end_season(actor: Player) -> Season:
    """Close the open season today and start the next one. Today's Sunday stays in the old one."""
    if not actor.is_board:
        raise PermissionDenied
    current = Season.objects.select_for_update().filter(ends_on=None).first()
    if current is None:
        current = seasons.current_season()
    today = clock.now().date()
    last_end = Season.objects.filter(ends_on__isnull=False).order_by("-ends_on").first()
    if last_end is not None and last_end.ends_on >= today:
        raise CommunityError("A temporada anterior foi encerrada hoje. Espere até amanhã.")
    current.ends_on = today
    current.ended_by = actor
    current.save(update_fields=["ends_on", "ended_by"])
    return Season.objects.create(number=current.number + 1)
