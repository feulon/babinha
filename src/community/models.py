from django.conf import settings
from django.db import models

from core import clock
from games.models import GameDay


class Season(models.Model):
    """A season runs until the diretoria ends it by hand. Exactly one is open at a time.

    A finished season holds the days up to and including `ends_on`.
    """

    number = models.PositiveSmallIntegerField("número", unique=True)
    ends_on = models.DateField("encerrada em", null=True, blank=True)
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="encerrada por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        verbose_name = "temporada"
        ordering = ["number"]
        constraints = [
            # At most one open season (ends_on empty) and one season per end date.
            models.UniqueConstraint(
                fields=["ends_on"], nulls_distinct=False, name="one_open_season"
            ),
            models.CheckConstraint(
                condition=models.Q(ends_on=None, ended_by=None)
                | models.Q(ends_on__isnull=False, ended_by__isnull=False),
                name="finished_seasons_have_end_and_author",
            ),
        ]

    def __str__(self) -> str:
        return f"Temporada {self.number}"

    @property
    def is_open(self) -> bool:
        return self.ends_on is None


class Ballot(models.Model):
    """Marks that a player already voted on a Sunday.

    It is deliberately not linked to the picks: the vote is anonymous, so what the voter chose
    only exists as `PlaceTally` counters.
    """

    game_day = models.ForeignKey(GameDay, on_delete=models.CASCADE, related_name="ballots")
    voter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        verbose_name = "voto registrado"
        verbose_name_plural = "votos registrados"
        constraints = [
            models.UniqueConstraint(fields=["game_day", "voter"], name="one_ballot_per_voter"),
        ]

    def __str__(self) -> str:
        return f"{self.voter} votou em {self.game_day}"


class PlaceTally(models.Model):
    """How many times a player was picked for a place (1st, 2nd or 3rd) on a Sunday."""

    game_day = models.ForeignKey(GameDay, on_delete=models.CASCADE, related_name="tallies")
    nominee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    place = models.PositiveSmallIntegerField("lugar")
    votes = models.PositiveIntegerField("votos", default=0)

    class Meta:
        verbose_name = "votos por lugar"
        verbose_name_plural = "votos por lugar"
        constraints = [
            models.UniqueConstraint(
                fields=["game_day", "nominee", "place"], name="one_tally_per_place"
            ),
            models.CheckConstraint(
                condition=models.Q(place__gte=1, place__lte=3), name="tally_place_range"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.nominee}: {self.place}º x{self.votes}"
