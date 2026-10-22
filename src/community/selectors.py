"""Read models for the community screens: the vote and the season table."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count

from accounts.models import Player
from community import seasons
from community.domain import ranking, stats, voting
from community.facts import load_days
from community.models import Ballot, PlaceTally, Season
from core import clock, demo
from games.domain.schedule import last_game_date
from games.models import GameDay
from games.services import schedule_for
from matchday.models import Match

HOME_CARD_DAYS = 7


# The Bola de Ouro of a Sunday


def windows(game_days: Iterable[GameDay], now: datetime | None = None) -> dict[int, voting.Window]:
    """The voting window of each Sunday, in a handful of queries."""
    now = now or clock.now()
    days = list(game_days)
    ids = [day.pk for day in days]
    confirmed = set(
        GameDay.objects.filter(pk__in=ids, lineup__confirmed_at__isnull=False).values_list(
            "pk", flat=True
        )
    )
    counts = {
        row["lineup__game_day_id"]: row
        for row in Match.objects.filter(lineup__game_day_id__in=ids)
        .values("lineup__game_day_id")
        .annotate(total=Count("pk"))
    }
    voted_out = everyone_voted(days) if demo.is_on() else set()
    result = {}
    for day in days:
        row = counts.get(day.pk, {"total": 0})
        schedule = schedule_for(day.date)
        result[day.pk] = voting.window(
            now=now,
            closes_at=schedule.vote_closes,
            has_game=day.pk in confirmed and not day.is_cancelled,
            matches=row["total"],
            # Use the same automatic Sunday end as the existing game-day lifecycle.
            # Demo Sundays only end through the diretoria, regardless of the real clock.
            game_over_at=day.finished_at or (None if demo.is_on() else schedule.charges_open),
            everyone_voted=day.pk in voted_out,
        )
    return result


def everyone_voted(game_days: Iterable[GameDay]) -> set[int]:
    """Demo mode: Sundays where every player who played has voted, which closes the vote the way
    Monday morning does in the real app."""
    days = list(game_days)
    ballots = Counter(
        Ballot.objects.filter(game_day__in=days).values_list("game_day_id", flat=True)
    )
    return {
        fact.day_id
        for fact in load_days(days)
        if fact.attendees and ballots[fact.day_id] >= len(fact.attendees)
    }


def window_of(game_day: GameDay, now: datetime | None = None) -> voting.Window:
    return windows([game_day], now)[game_day.pk]


def eligible_voters(game_day: GameDay) -> list[Player]:
    """Who played the Sunday: they vote and can be voted for."""
    attendees = {pk for fact in load_days([game_day]) for pk in fact.attendees}
    return sorted(Player.objects.filter(pk__in=attendees), key=lambda p: p.name.casefold())


def day_standings(game_day_ids: Iterable[int]) -> dict[int, tuple[voting.Standing, ...]]:
    tallies: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    rows = PlaceTally.objects.filter(game_day_id__in=list(game_day_ids), votes__gt=0)
    for day_id, nominee_id, place, votes in rows.values_list(
        "game_day_id", "nominee_id", "place", "votes"
    ):
        tallies[day_id].append((nominee_id, place, votes))
    return {day_id: voting.standings(rows) for day_id, rows in tallies.items()}


def closed_standings(
    game_days: Iterable[GameDay], now: datetime | None = None
) -> dict[int, tuple[voting.Standing, ...]]:
    """The final standings of each Sunday whose vote is closed and got any votes."""
    days = list(game_days)
    table = windows(days, now)
    return day_standings([d.pk for d in days if table[d.pk].phase == voting.Phase.CLOSED])


def gold_ball_winners(
    game_days: Iterable[GameDay], now: datetime | None = None
) -> dict[int, tuple[int, ...]]:
    """Winners per Sunday whose vote is closed. A Sunday nobody voted on has none."""
    return {
        day_id: tuple(row.player_id for row in voting.winners(rows))
        for day_id, rows in closed_standings(game_days, now).items()
    }


@dataclass(frozen=True)
class Result:
    player: Player
    standing: voting.Standing
    is_winner: bool
    position: int  # 1 for the winners; ties share a position


@dataclass(frozen=True)
class VotingView:
    game_day: GameDay
    window: voting.Window
    nominees: tuple[Player, ...]  # who the viewer can vote for
    can_vote: bool
    has_voted: bool
    played: bool  # the viewer played that Sunday
    ballots_cast: int  # how many have voted: only the count, never who
    voters_total: int  # how many can vote
    results: tuple[Result, ...]  # only once the vote is closed

    @property
    def shows_ballot(self) -> bool:
        """The ballot form is on screen (the other states only explain why it is not)."""
        return (
            self.window.phase == voting.Phase.OPEN
            and self.played
            and self.can_vote
            and not self.has_voted
        )

    @property
    def winners(self) -> tuple[Player, ...]:
        return tuple(result.player for result in self.results if result.is_winner)


def voting_view(game_day: GameDay, viewer: Player) -> VotingView:
    window = window_of(game_day)
    voters = eligible_voters(game_day) if window.phase != voting.Phase.NONE else []
    played = any(p.pk == viewer.pk for p in voters)
    has_voted = Ballot.objects.filter(game_day=game_day, voter=viewer).exists()
    nominees = tuple(p for p in voters if p.pk != viewer.pk)
    results: tuple[Result, ...] = ()
    if window.phase == voting.Phase.CLOSED:
        ranked = day_standings([game_day.pk]).get(game_day.pk, ())
        names = Player.objects.in_bulk([row.player_id for row in ranked])
        top = {row.player_id for row in voting.winners(ranked)}
        results = tuple(
            Result(
                names[r.player_id],
                r,
                r.player_id in top,
                1 + sum(1 for other in ranked if other.sort_key > r.sort_key),
            )
            for r in ranked
        )
    return VotingView(
        game_day=game_day,
        window=window,
        nominees=nominees,
        can_vote=window.is_open
        and played
        and not has_voted
        and len(nominees) >= len(voting.PLACES),
        has_voted=has_voted,
        played=played,
        ballots_cast=Ballot.objects.filter(game_day=game_day).count(),
        voters_total=len(voters),
        results=results,
    )


def latest_voting_day(now: datetime | None = None) -> GameDay | None:
    """The most recent Sunday that has (or had) a vote. A Sunday still to come has none."""
    now = now or clock.now()
    candidates = list(
        GameDay.objects.filter(date__lte=last_game_date(now), lineup__confirmed_at__isnull=False)
        .filter(cancelled_at=None)
        .order_by("-date")[:4]
    )
    table = windows(candidates, now)
    return next((d for d in candidates if table[d.pk].phase != voting.Phase.NONE), None)


def open_voting_day(now: datetime | None = None) -> GameDay | None:
    """The Sunday whose Bola de Ouro can be voted on right now, if any."""
    now = now or clock.now()
    day = latest_voting_day(now)
    if day is None or not window_of(day, now).is_open:
        return None
    return day


def past_gold_balls(now: datetime | None = None) -> list[tuple[GameDay, tuple[Player, ...]]]:
    """Sundays with a closed vote and who won, most recent first."""
    now = now or clock.now()
    days = list(
        GameDay.objects.filter(
            date__lte=last_game_date(now), cancelled_at=None, lineup__confirmed_at__isnull=False
        ).order_by("-date")
    )
    winners = gold_ball_winners(days, now)
    people = Player.objects.in_bulk({p for ids in winners.values() for p in ids})
    return [(day, tuple(people[p] for p in winners[day.pk])) for day in days if winners.get(day.pk)]


@dataclass(frozen=True)
class HomeCard:
    view: VotingView


def home_card(viewer: Player, now: datetime | None = None) -> HomeCard | None:
    """What the home screen says about the Bola de Ouro, if anything is worth saying."""
    now = now or clock.now()
    day = latest_voting_day(now)
    if day is None:
        return None
    view = voting_view(day, viewer)
    if view.window.is_open and (view.can_vote or view.has_voted):
        return HomeCard(view)
    recent = (now.date() - day.date).days < HOME_CARD_DAYS
    if view.window.phase == voting.Phase.CLOSED and view.results and recent:
        return HomeCard(view)
    return None


def open_votes_in(season: Season) -> list[GameDay]:
    """Sundays of the season whose Bola de Ouro is still being decided."""
    days = list(seasons.game_days_of(season).filter(lineup__confirmed_at__isnull=False))
    table = windows(days)
    return [d for d in days if table[d.pk].phase in (voting.Phase.WAITING, voting.Phase.OPEN)]


# The season table


@dataclass(frozen=True)
class TableRow:
    position: int
    player: Player
    totals: stats.Totals


@dataclass(frozen=True)
class SeasonTable:
    season: Season | None  # None is the whole history
    metric: ranking.Metric
    rows: tuple[TableRow, ...]
    sundays: int  # Sundays played


def totals_for(season: Season | None) -> tuple[dict[int, stats.Totals], int]:
    if season is None:
        days = list(GameDay.objects.filter(cancelled_at=None).order_by("-date"))
    else:
        days = list(seasons.game_days_of(season))
    facts = load_days(days)
    table = stats.totals(facts, gold_ball_winners(days))
    return table, sum(1 for fact in facts if fact.played)


def season_table(season: Season | None, metric: ranking.Metric) -> SeasonTable:
    table, played = totals_for(season)
    players = Player.objects.in_bulk(table.keys())
    names = {pk: player.name for pk, player in players.items()}
    rows = tuple(
        TableRow(row.position, players[row.player_id], row.totals)
        for row in ranking.rank(table, metric, names)
    )
    return SeasonTable(season, metric, rows, played)
