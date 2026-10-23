"""The diretoria's rules for drawing a Sunday's teams: who must play together.

Two rules, both per Sunday: first-timers play with whoever invited them (a switch, with a release
for one player), and groups of two to five line players the diretoria wants in the same team.
When the rules cannot be honoured, `resolutions` lists the ways out and `resolve_conflict` applies
one and draws.

Like `matchday.services`, every operation locks the Sunday's GameDay row first.
"""

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from django.db import transaction

from accounts.models import Player
from matchday.domain.draw import (
    TEAM_SIZE,
    Conflict,
    DrawConflict,
    DrawError,
    DrawPlayer,
    draw_teams,
    team_sizes,
)
from matchday.domain.groups import Group, build_groups
from matchday.domain.packing import oversized_capacities
from matchday.grouping import Grouping, RookieRow
from matchday.models import Lineup, RookieRelease, TeamRules, TogetherGroup, TogetherMember
from matchday.services import (
    MatchdayError,
    draw,
    grouping_of,
    lock_game_day,
    require_board,
)

MAX_GROUP_SIZE = TEAM_SIZE
GROUP_NAME_MAX_LENGTH = TogetherGroup._meta.get_field("name").max_length

REMOVE = "remover"
RELEASE = "liberar"
ACCEPT = "aceitar"


def _rules(game_day_id: int) -> TeamRules:
    return TeamRules.objects.get_or_create(game_day_id=game_day_id)[0]


def _rules_changed(game_day_id: int) -> TeamRules:
    """The rules row after a change: a team bigger than the others was accepted for the old rules,
    so that acceptance is not carried over."""
    rules = _rules(game_day_id)
    if rules.allow_oversized_team:
        rules.allow_oversized_team = False
        rules.save(update_fields=["allow_oversized_team"])
    return rules


@transaction.atomic
def set_rookie_rule(game_day_id: int, actor: Player, enabled: bool) -> TeamRules:
    """Turn the first-timer rule on or off for the Sunday."""
    require_board(actor)
    lock_game_day(game_day_id)
    rules = _rules_changed(game_day_id)
    rules.rookies_follow_inviter = enabled
    rules.save(update_fields=["rookies_follow_inviter"])
    return rules


@transaction.atomic
def create_group(
    game_day_id: int, actor: Player, player_ids: Iterable[int], name: str = ""
) -> TogetherGroup:
    """Make a group of two to five line players who play in the same team."""
    require_board(actor)
    game_day = lock_game_day(game_day_id)
    ids = list(dict.fromkeys(player_ids))
    if not 2 <= len(ids) <= MAX_GROUP_SIZE:
        raise MatchdayError(f"Escolha de 2 a {MAX_GROUP_SIZE} jogadores para o grupo.")
    grouping = grouping_of(game_day)
    names = grouping.names
    if any(pid not in names for pid in ids):
        raise MatchdayError("Só jogadores de linha da lista do domingo entram num grupo.")
    taken = {player.pk for group in grouping.manual for player in group.players}
    already = [names[pid] for pid in ids if pid in taken]
    if already:
        raise MatchdayError(
            f"{', '.join(already)} já está{'' if len(already) == 1 else 'o'} "
            "num grupo. Desfaça o grupo antes de montar outro."
        )
    cleaned = " ".join(name.split())
    if len(cleaned) > GROUP_NAME_MAX_LENGTH:
        raise MatchdayError(f"Nome de grupo com no máximo {GROUP_NAME_MAX_LENGTH} caracteres.")

    _rules_changed(game_day_id)
    group = TogetherGroup.objects.create(game_day=game_day, name=cleaned, created_by=actor)
    TogetherMember.objects.bulk_create(
        TogetherMember(group=group, game_day=game_day, player_id=pid) for pid in ids
    )
    return group


@transaction.atomic
def delete_group(game_day_id: int, actor: Player, group_id: int) -> None:
    require_board(actor)
    lock_game_day(game_day_id)
    deleted, _ = TogetherGroup.objects.filter(pk=group_id, game_day_id=game_day_id).delete()
    if not deleted:
        raise MatchdayError("Grupo não encontrado.")
    _rules_changed(game_day_id)


def _rookie_row(grouping: Grouping, player_id: int) -> RookieRow:
    row = next((r for r in grouping.rookie_rows if r.player.pk == player_id), None)
    if row is None:
        raise MatchdayError("Esse jogador não é estreante com convite neste domingo.")
    return row


@transaction.atomic
def release_rookie(game_day_id: int, actor: Player, player_id: int) -> None:
    """Let a first-timer play apart from who invited them, on this Sunday only."""
    require_board(actor)
    game_day = lock_game_day(game_day_id)
    row = _rookie_row(grouping_of(game_day), player_id)
    if row.state != "applies":
        raise MatchdayError(f"{row.player.display_name} já está livre neste domingo.")
    _apply_release(game_day_id, actor, player_id)


def _apply_release(game_day_id: int, actor: Player, player_id: int) -> None:
    _rules_changed(game_day_id)
    RookieRelease.objects.get_or_create(
        game_day_id=game_day_id, player_id=player_id, defaults={"released_by": actor}
    )


@transaction.atomic
def restore_rookie(game_day_id: int, actor: Player, player_id: int) -> None:
    """Bind a released first-timer to who invited them again."""
    require_board(actor)
    lock_game_day(game_day_id)
    deleted, _ = RookieRelease.objects.filter(game_day_id=game_day_id, player_id=player_id).delete()
    if not deleted:
        raise MatchdayError("Esse estreante não foi liberado.")
    _rules_changed(game_day_id)


# Conflicts


@dataclass(frozen=True)
class Resolution:
    """A way out of a conflict, with what it leads to and, once applied, what changed."""

    key: str
    title: str
    effect: str
    done: str = ""  # "Bigode saiu do grupo.": said after the way out is applied
    suggested: bool = False


def _sizes_text(sizes: Iterable[int]) -> str:
    return " · ".join(str(size) for size in sizes)


def _conflicts(
    grouping: Grouping, groups: Sequence[Group], *, allow_oversized: bool = False
) -> tuple[Conflict, ...]:
    """Why the teams cannot be drawn with `groups`; empty when they can (the dice never matter)."""
    pool = [DrawPlayer(player.pk, player.pote) for player in grouping.line]
    try:
        draw_teams(
            pool,
            random.Random(0),
            groups=[group.player_ids for group in groups],
            allow_oversized=allow_oversized,
            balance=False,
        )
    except DrawConflict as conflict:
        return conflict.conflicts
    except DrawError:  # too few players and the like: nothing to do with the groups
        return ()
    return ()


def conflicts_of(grouping: Grouping) -> tuple[Conflict, ...]:
    """Why the groups cannot be dealt right now; empty when they can."""
    return _conflicts(
        grouping, grouping.groups, allow_oversized=grouping.rules.allow_oversized_team
    )


def describe(grouping: Grouping, conflict: Conflict) -> str:
    """What is wrong, in a sentence for the diretoria."""
    if conflict.kind == "goal":
        return (
            f"Com {len(grouping.line)} jogadores de linha, alguém vai ao gol e só sobram "
            "grupos inteiros para isso. Nenhum grupo pode ser separado."
        )
    if conflict.kind == "no_fit":
        groups = "; ".join(" + ".join(grouping.names_of(group)) for group in conflict.groups)
        return (
            f"Cada grupo cabe num time, mas juntos não cabem: {groups}. "
            f"Cada time tem {conflict.capacity} vagas."
        )
    (members,) = conflict.groups
    group = next((g for g in grouping.groups if g.player_ids == members), None)
    size = len(members)
    return (
        f"O grupo {_group_text(grouping, members, group)} tem {size} jogadores. "
        f"Cada time tem {conflict.capacity} vagas."
    )


def _group_text(grouping: Grouping, members: tuple[int, ...], group: Group | None) -> str:
    names = grouping.names
    manual = {p.pk for mg in grouping.manual for p in mg.players if p.pk in members}
    extras = [(r, i) for r, i in (group.bonds if group else ()) if r not in manual]
    if not manual or not extras:
        return ", ".join(names[pid] for pid in members)
    bound = " e ".join(f"{names[r]} (joga com {names[i]})" for r, i in extras)
    label = "o estreante" if len(extras) == 1 else "os estreantes"
    return f"{', '.join(names[pid] for pid in members if pid in manual)} mais {label} {bound}"


def resolutions(grouping: Grouping, conflicts: Sequence[Conflict]) -> tuple[Resolution, ...]:
    """The ways out of `conflicts`, the suggested one first.

    Letting a first-timer play in another team comes first: it costs only that player's bond, for
    this Sunday, and nobody leaves a group the diretoria made.
    """
    involved = {pid for conflict in conflicts for group in conflict.groups for pid in group}
    options = [
        option
        for option in (
            _release_rookie(grouping, involved),
            _remove_member(grouping, involved),
            _accept_bigger_team(grouping),
        )
        if option is not None
    ]
    return tuple(replace(option, suggested=(n == 0)) for n, option in enumerate(options))


def _after(grouping: Grouping, manual: dict[int, list[int]], bonds: dict[int, int]) -> list[Group]:
    return list(build_groups(manual, bonds, [p.pk for p in grouping.line]))


def _bonds(grouping: Grouping) -> dict[int, int]:
    return dict(bond for group in grouping.groups for bond in group.bonds)


def _remove_member(grouping: Grouping, involved: set[int]) -> Resolution | None:
    anchors = set(_bonds(grouping).values())
    candidates = [
        (member, group)
        for group in grouping.manual
        for member in reversed(group.players)  # the last one added goes first
        if member.pk in involved
    ]
    candidates.sort(key=lambda candidate: candidate[0].pk in anchors)  # stable: anchors last
    for member, group in candidates:
        manual = {
            g.id: [p.pk for p in g.players if p.pk != member.pk or g.id != group.id]
            for g in grouping.manual
        }
        if not _conflicts(grouping, _after(grouping, manual, _bonds(grouping))):
            done = f"{member.display_name} saiu do grupo."
            if len(group.players) <= 2:
                done += " O grupo foi desfeito."
            return Resolution(
                f"{REMOVE}:{member.pk}",
                f"Tirar {member.display_name} do grupo",
                f"Sorteio possível: {_sizes_text(team_sizes(len(grouping.line)))}.",
                done,
            )
    return None


def _release_rookie(grouping: Grouping, involved: set[int]) -> Resolution | None:
    bonds = _bonds(grouping)
    manual = {g.id: [p.pk for p in g.players] for g in grouping.manual}
    names = grouping.names
    for rookie in sorted(bonds):
        if rookie not in involved:
            continue
        remaining = {r: i for r, i in bonds.items() if r != rookie}
        if not _conflicts(grouping, _after(grouping, manual, remaining)):
            return Resolution(
                f"{RELEASE}:{rookie}",
                f"Deixar {names[rookie]} jogar em outro time",
                f"{names[rookie]} não precisa ficar com {names[bonds[rookie]]}, só neste "
                f"domingo. Sorteio possível: {_sizes_text(team_sizes(len(grouping.line)))}.",
                f"{names[rookie]} vai poder jogar em outro time, só neste domingo.",
            )
    return None


def _accept_bigger_team(grouping: Grouping) -> Resolution | None:
    if _conflicts(grouping, grouping.groups, allow_oversized=True):
        return None
    sizes = [group.size for group in grouping.groups]
    capacities = oversized_capacities(team_sizes(len(grouping.line)), sizes)
    biggest = max(sizes, default=0)
    return Resolution(
        ACCEPT,
        f"Deixar um time com {biggest}" if biggest > TEAM_SIZE else "Deixar um time maior",
        f"Sorteio possível: {_sizes_text(capacities)}. O aviso de desequilíbrio fica nos ajustes.",
        f"Um time vai jogar com {biggest} jogadores.",
    )


def resolution_for(grouping: Grouping, action: str) -> Resolution | None:
    """The way out `action` names, if the conflict the groups are in now offers it."""
    conflicts = conflicts_of(grouping)
    return next((r for r in resolutions(grouping, conflicts) if r.key == action), None)


@dataclass(frozen=True)
class RulesProblem:
    """Why the rules cannot be dealt, with the ways out, as the draw screen shows it."""

    lines: tuple[str, ...]
    resolutions: tuple[Resolution, ...]


def rules_problem(grouping: Grouping) -> RulesProblem | None:
    """What stops the draw; None when the groups can be dealt."""
    conflicts = conflicts_of(grouping)
    if not conflicts:
        return None
    return RulesProblem(
        tuple(describe(grouping, conflict) for conflict in conflicts),
        resolutions(grouping, conflicts),
    )


@transaction.atomic
def resolve_conflict(
    game_day_id: int, actor: Player, action: str, rng: random.Random | None = None
) -> Lineup:
    """Apply one of the ways out of the draw's conflict, then draw.

    `action` must be one of the keys `resolutions` offers now: a stale form (the list or the
    rules changed meanwhile) is refused instead of doing something unexpected.
    """
    require_board(actor)
    game_day = lock_game_day(game_day_id)
    grouping = grouping_of(game_day)
    conflicts = conflicts_of(grouping)
    if conflicts:
        offered = {option.key for option in resolutions(grouping, conflicts)}
        if action not in offered:
            raise MatchdayError("Essa saída não vale mais. Confira o sorteio de novo.")
        _apply(game_day_id, actor, action)
    return draw(game_day_id, actor, rng)


def _apply(game_day_id: int, actor: Player, action: str) -> None:
    kind, _, raw = action.partition(":")
    if kind == ACCEPT:
        rules = _rules(game_day_id)
        rules.allow_oversized_team = True
        rules.save(update_fields=["allow_oversized_team"])
    elif kind == RELEASE:
        _apply_release(game_day_id, actor, int(raw))
    elif kind == REMOVE:
        member = TogetherMember.objects.select_related("group").get(
            game_day_id=game_day_id, player_id=int(raw)
        )
        group = member.group
        member.delete()
        if group.members.count() < 2:
            group.delete()
        _rules_changed(game_day_id)
    else:  # pragma: no cover - resolve_conflict only passes keys `resolutions` offered
        raise MatchdayError("Saída desconhecida.")
