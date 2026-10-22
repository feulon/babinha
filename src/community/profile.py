"""Read models for the player profile and the player directory.

Everything here comes from the same facts as the ranking (`load_days`), so a profile always
agrees with the season table. Nothing in it reveals who anyone voted for: the Bola de Ouro
only enters through the closed standings of each Sunday.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from accounts.models import Player, PlayerType, Position
from community import seasons, selectors
from community.domain import stats
from community.domain.profile import (
    RUNNER_UP_PLACES,
    WINNER,
    MatchRow,
    match_rows,
    podium_places,
    presence_percent,
)
from community.domain.seasons import season_index
from community.facts import load_days
from community.models import Season
from core import clock
from finance.selectors import PlayerPayments, player_payments
from games.models import GameDay
from matchday.models import LineupSpot

SUMMARY_SUNDAYS = 3


@dataclass(frozen=True)
class SundayRow:
    game_day: GameDay
    season: int
    team: str  # the team's name, or "No gol"
    line: stats.Line
    place: int | None  # place on the closed Bola de Ouro vote, if the player got votes
    matches: tuple[MatchRow, ...]

    @property
    def won_gold_ball(self) -> bool:
        return self.place == WINNER

    @property
    def runner_up_place(self) -> int | None:
        return self.place if self.place in RUNNER_UP_PLACES else None


@dataclass(frozen=True)
class Summary:
    season: Season | None  # None is the whole history
    totals: stats.Totals
    possible: int  # Sundays played by the baba since the player's first one
    recent: tuple[SundayRow, ...]

    @property
    def presence(self) -> int | None:
        return presence_percent(self.totals.sundays, self.possible)

    @property
    def goals_per_sunday(self) -> float | None:
        if not self.totals.sundays:
            return None
        return round(self.totals.goals / self.totals.sundays, 1)


@dataclass(frozen=True)
class Referral:
    player: Player
    sundays: int  # Sundays the referred player has played


@dataclass(frozen=True)
class Origin:
    referred_by: Player | None
    joined_on: date
    first_played: date | None
    referrals: tuple[Referral, ...]


@dataclass(frozen=True)
class PlayerProfile:
    player: Player
    sundays: tuple[SundayRow, ...]  # most recent first
    overall: stats.Totals
    first_played: date | None
    _facts: tuple[stats.DayFact, ...]
    _winners: Mapping[int, tuple[int, ...]]
    _season_of_day: Mapping[int, int]
    _everyone: Mapping[int, stats.Totals]

    @property
    def gold_balls(self) -> tuple[SundayRow, ...]:
        return tuple(row for row in self.sundays if row.won_gold_ball)

    @property
    def runner_ups(self) -> dict[int, tuple[SundayRow, ...]]:
        """The Sundays the player finished 2nd and 3rd on the vote."""
        return {
            place: tuple(row for row in self.sundays if row.runner_up_place == place)
            for place in RUNNER_UP_PLACES
        }

    def summary(self, season: Season | None) -> Summary:
        in_scope = [
            fact
            for fact in self._facts
            if season is None or self._season_of_day[fact.day_id] == season.number
        ]
        ids = {fact.day_id for fact in in_scope}
        winners = {day_id: ids_ for day_id, ids_ in self._winners.items() if day_id in ids}
        totals = stats.totals(in_scope, winners).get(self.player.pk, stats.Totals())
        first = self.first_played
        possible = sum(1 for fact in in_scope if fact.played and first and fact.date >= first)
        recent = tuple(
            row for row in self.sundays if season is None or row.season == season.number
        )[:SUMMARY_SUNDAYS]
        return Summary(season, totals, possible, recent)

    def origin(self) -> Origin:
        referrals = tuple(
            Referral(person, self._everyone.get(person.pk, stats.Totals()).sundays)
            for person in self.player.referrals.order_by("created_at", "pk")
        )
        return Origin(self.player.referred_by, self.player.joined_on, self.first_played, referrals)


def load(player: Player) -> PlayerProfile:
    days = list(GameDay.objects.filter(cancelled_at=None).order_by("-date"))
    facts = load_days(days)
    standings = selectors.closed_standings(days)
    winners = selectors.gold_ball_winners(days)
    by_id = {day.pk: day for day in days}

    everyone = seasons.all_seasons()
    ends = [s.ends_on for s in everyone if s.ends_on is not None]
    season_of_day = {day.pk: everyone[season_index(day.date, ends)].number for day in days}

    team_names = {
        spot.lineup.game_day_id: spot.team.name if spot.team else "No gol"
        for spot in LineupSpot.objects.filter(player=player).select_related("team", "lineup")
    }
    rows = []
    for fact in facts:
        line = stats.day_lines(fact).get(player.pk)
        if line is None:
            continue
        places = podium_places(standings.get(fact.day_id, ()))
        rows.append(
            SundayRow(
                game_day=by_id[fact.day_id],
                season=season_of_day[fact.day_id],
                team=team_names.get(fact.day_id, "-"),
                line=line,
                place=places.get(player.pk),
                matches=match_rows(fact, player.pk),
            )
        )
    table = stats.totals(facts, winners)
    return PlayerProfile(
        player=player,
        sundays=tuple(rows),
        overall=table.get(player.pk, stats.Totals()),
        first_played=rows[-1].game_day.date if rows else None,
        _facts=tuple(facts),
        _winners=winners,
        _season_of_day=season_of_day,
        _everyone=table,
    )


# What only the player and the diretoria see


@dataclass(frozen=True)
class PrivateSummary:
    payments: PlayerPayments


def private_summary(subject: Player) -> PrivateSummary:
    return PrivateSummary(payments=player_payments(subject, clock.now().date()))


# The directory

KINDS = {
    "todos": "Todos",
    PlayerType.MENSALISTA.value: "Mensalistas",
    PlayerType.AVULSO.value: "Avulsos",
    PlayerType.AGREGADO.value: "Agregados",
    Position.GOLEIRO.value: "Goleiros",
}
DEFAULT_KIND = "todos"


@dataclass(frozen=True)
class DirectoryRow:
    player: Player
    sundays: int
    gold_balls: int


def directory(query: str = "", kind: str = DEFAULT_KIND) -> tuple[DirectoryRow, ...]:
    """The active players, by name, filtered by type or position and by part of the name."""
    people = Player.objects.filter(is_active=True).with_enrollment()
    if query:
        people = people.filter(name__icontains=query)
    if kind in PlayerType.values:
        people = people.of_type(kind)
    elif kind == Position.GOLEIRO.value:
        people = people.filter(position=Position.GOLEIRO)
    table, _ = selectors.totals_for(None)
    empty = stats.Totals()
    return tuple(
        DirectoryRow(p, table.get(p.pk, empty).sundays, table.get(p.pk, empty).gold_balls)
        for p in people
    )
