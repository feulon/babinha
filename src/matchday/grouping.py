"""Who must play together on a Sunday: first-timers with whoever invited them, and the groups the
diretoria made by hand.

Reads only. A first-timer's bond is derived each time from who is on the list, never stored, so
when the inviter leaves the list the first-timer is free on their own.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from django.db.models import Min

from accounts.models import Player
from games.models import Attendance, GameDay, ListEntry
from matchday.domain.groups import Group, build_groups, rookie_bonds
from matchday.models import RookieRelease, TeamRules, TogetherGroup

RookieState = Literal["applies", "absent", "goalkeeper", "released", "off"]


@dataclass(frozen=True)
class RookieRow:
    """A first-timer who was invited by someone, and what the rule does about it today."""

    player: Player
    inviter: Player
    state: RookieState


@dataclass(frozen=True)
class ManualGroup:
    id: int
    name: str
    players: tuple[Player, ...]  # who is playing today, by the order they were added

    @property
    def label(self) -> str:
        return self.name or ("Dupla" if len(self.players) == 2 else "Grupo")


@dataclass(frozen=True)
class Grouping:
    """Every tie between the line players of a Sunday."""

    rules: TeamRules
    line: tuple[Player, ...]
    groups: tuple[Group, ...]
    rookie_rows: tuple[RookieRow, ...]
    manual: tuple[ManualGroup, ...]
    released_ids: frozenset[int]
    rookie_ids: frozenset[int] = frozenset()  # first-timers among everyone on the list, keepers too

    @property
    def names(self) -> dict[int, str]:
        return {player.pk: player.name for player in self.line}

    def group_of(self, player_id: int) -> Group | None:
        return next((group for group in self.groups if player_id in group.player_ids), None)

    def names_of(self, player_ids: Iterable[int]) -> list[str]:
        names = self.names
        return [names[pid] for pid in player_ids]


def rules_for(game_day: GameDay) -> TeamRules:
    """The Sunday's rules; the defaults (unsaved) when the diretoria never touched them."""
    return TeamRules.objects.filter(game_day=game_day).first() or TeamRules(game_day=game_day)


def first_timers(game_day: GameDay, player_ids: Iterable[int]) -> set[int]:
    """Who has never played with the group: no presence on an earlier Sunday and not in the baba
    since before the app's first Sunday (the diretoria says so in "No baba desde")."""
    ids = set(player_ids)
    veterans = set(
        ListEntry.objects.filter(
            player_id__in=ids, attendance=Attendance.PRESENTE, game_day__date__lt=game_day.date
        ).values_list("player_id", flat=True)
    )
    first_sunday = GameDay.objects.aggregate(first=Min("date"))["first"] or game_day.date
    veterans |= set(
        Player.objects.filter(pk__in=ids, member_since__lt=first_sunday).values_list(
            "pk", flat=True
        )
    )
    return ids - veterans


def grouping_for(
    game_day: GameDay, line: Sequence[Player], goalkeepers: Sequence[Player]
) -> Grouping:
    """The ties among `line`, the line players on the Sunday's list (`goalkeepers` are the rest)."""
    line = tuple(line)
    playing = {player.pk for player in line}
    rules = rules_for(game_day)
    released = frozenset(
        RookieRelease.objects.filter(game_day=game_day, player_id__in=playing).values_list(
            "player_id", flat=True
        )
    )

    rookie_ids = frozenset(first_timers(game_day, (p.pk for p in (*line, *goalkeepers))))
    rookie_rows = _rookie_rows(line, rookie_ids, {g.pk for g in goalkeepers}, rules, released)
    bonds = (
        rookie_bonds(
            playing,
            {row.player.pk: row.inviter.pk for row in rookie_rows},
            [row.player.pk for row in rookie_rows],
            released,
        )
        if rules.rookies_follow_inviter
        else {}
    )
    manual = _manual_groups(game_day, {player.pk: player for player in line})
    groups = build_groups({g.id: [p.pk for p in g.players] for g in manual}, bonds, playing)
    return Grouping(rules, line, groups, rookie_rows, manual, released, rookie_ids)


def _rookie_rows(
    line: Sequence[Player],
    rookie_ids: frozenset[int],
    keepers: set[int],
    rules: TeamRules,
    released: frozenset[int],
) -> tuple[RookieRow, ...]:
    invited = [player for player in line if player.referred_by is not None]
    playing = {player.pk for player in line}

    def state(player: Player) -> RookieState:
        inviter_id = player.referred_by_id
        if not rules.rookies_follow_inviter:
            return "off"
        if player.pk in released:
            return "released"
        if inviter_id in playing:
            return "applies"
        return "goalkeeper" if inviter_id in keepers else "absent"

    return tuple(
        RookieRow(player, player.referred_by, state(player))
        for player in invited
        if player.pk in rookie_ids
    )


def _manual_groups(game_day: GameDay, line_by_id: dict[int, Player]) -> tuple[ManualGroup, ...]:
    groups = TogetherGroup.objects.filter(game_day=game_day).prefetch_related("members")
    found = []
    for group in groups:
        members = tuple(
            line_by_id[member.player_id]
            for member in group.members.all()
            if member.player_id in line_by_id
        )
        if members:
            found.append(ManualGroup(group.pk, group.name, members))
    return tuple(found)
