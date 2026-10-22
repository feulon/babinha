"""Seasons as stored: which one is open, and which Sundays belong to which."""

from datetime import date

from django.db import IntegrityError, transaction
from django.db.models import Max, QuerySet

from community.domain.seasons import season_index
from community.models import Season
from games.models import GameDay


def current_season() -> Season:
    """The open season. The very first one is created the first time anyone asks."""
    season = Season.objects.filter(ends_on=None).first()
    if season is not None:
        return season
    number = (Season.objects.aggregate(last=Max("number"))["last"] or 0) + 1
    try:
        with transaction.atomic():
            return Season.objects.create(number=number)
    except IntegrityError:  # someone else created it first
        return Season.objects.get(ends_on=None)


def all_seasons() -> list[Season]:
    current_season()
    return list(Season.objects.all())


def season_of(day: date) -> Season:
    seasons = all_seasons()
    return seasons[season_index(day, [s.ends_on for s in seasons if s.ends_on is not None])]


def game_days_of(season: Season) -> QuerySet[GameDay]:
    """The Sundays of a season, most recent first."""
    days = GameDay.objects.filter(cancelled_at=None)
    previous = Season.objects.filter(number__lt=season.number, ends_on__isnull=False).last()
    if previous is not None:
        days = days.filter(date__gt=previous.ends_on)
    if season.ends_on is not None:
        days = days.filter(date__lte=season.ends_on)
    return days.order_by("-date")
