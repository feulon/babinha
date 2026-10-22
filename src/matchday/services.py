"""Game day operations: the team draw and its adjustments, the match table and the results.

Every operation first takes `SELECT ... FOR UPDATE` on the Sunday's GameDay row, like the list
operations in `games.services`, so two diretores editing at once are serialized.

Everything here stays editable by the diretoria at any time, because the football is informal:
a player may cover for someone in another team, a goal may be credited wrongly, a match may have
to be redone. The data stays consistent for the ranking: a goal credits the scorer and the team it
was recorded for, and `MatchPlayer` records who really played each match, so changing the
Sunday's lineup afterwards never rewrites matches already played.
"""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, F, Max, Q

from accounts.models import Player
from core import clock
from games.domain.types import EntryStatus, Position
from games.models import Attendance, GameDay, ListEntry
from games.services import game_started, schedule_for
from matchday.domain.draw import (
    TEAM_COUNT,
    Conflict,
    DrawConflict,
    DrawError,
    DrawPlayer,
    draw_teams,
    team_sizes,
)
from matchday.domain.groups import Group
from matchday.domain.queue import PlayedMatch, next_goalkeepers, next_matchup, waiting_line
from matchday.domain.score import MatchOver, Score, Side
from matchday.domain.sizes import goal_full_message, goal_is_over_full
from matchday.grouping import Grouping, grouping_for
from matchday.models import Goal, Lineup, LineupSpot, Match, MatchPlayer, Team

TEAM_NAME_MAX_LENGTH = Team._meta.get_field("name").max_length
_system_random = random.SystemRandom()


class MatchdayError(Exception):
    """A game day operation that is not allowed. `message` is shown to the diretoria (pt-BR)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RulesConflict(MatchdayError):
    """The teams cannot be drawn keeping the groups together. `conflicts` say why."""

    def __init__(self, conflicts: Sequence[Conflict]) -> None:
        super().__init__("Não dá para sortear com estas regras.")
        self.conflicts = tuple(conflicts)


def default_team_name(number: int) -> str:
    return f"Time {chr(ord('A') + number - 1)}"


def require_board(actor: Player) -> None:
    if not actor.is_board:
        raise PermissionDenied


def lock_game_day(game_day_id: int) -> GameDay:
    game_day = GameDay.objects.select_for_update().get(pk=game_day_id)
    if game_day.is_cancelled:
        raise MatchdayError("Este domingo foi cancelado.")
    return game_day


def _playing(game_day: GameDay, position: Position) -> list[ListEntry]:
    """Who holds a spot in `position`, in list order. Before the teams are confirmed, someone
    marked absent is out of the draw; once confirmed, the teams stay as published and a no-show
    only costs the fine (the absence is marked during the games)."""
    entries = ListEntry.objects.filter(
        game_day=game_day, status=EntryStatus.LISTA.value, position=position.value
    )
    lineup = lineup_for(game_day)
    if lineup is None or not lineup.is_confirmed:
        entries = entries.exclude(attendance=Attendance.FALTOU)
    return list(
        entries.select_related("player", "player__referred_by").order_by(
            "confirmed_at", "player_id"
        )
    )


def line_players(game_day: GameDay) -> list[Player]:
    """The line players the teams are drawn from."""
    return [entry.player for entry in _playing(game_day, Position.LINHA)]


def list_goalkeepers(game_day: GameDay) -> list[Player]:
    return [entry.player for entry in _playing(game_day, Position.GOLEIRO)]


def goalkeeper_pool(lineup: Lineup) -> list[Player]:
    """Who can keep goal in a match: the list's goalkeepers plus line players drawn to goal."""
    in_goal = [
        spot.player
        for spot in lineup.spots.filter(team=None).select_related("player").order_by("pk")
    ]
    return [*list_goalkeepers(lineup.game_day), *in_goal]


def match_goalkeeper_pool(match: Match) -> list[Player]:
    """The goalkeeper pool plus the match's recorded goalkeepers, so they stay choosable."""
    pool = goalkeeper_pool(match.lineup)
    recorded = [
        keeper
        for keeper in (match.home_goalkeeper, match.away_goalkeeper)
        if keeper is not None and keeper not in pool
    ]
    return [*pool, *recorded]


def grouping_of(game_day: GameDay) -> Grouping:
    """Who must play together on the Sunday, by the rules and groups set for it."""
    return grouping_for(game_day, line_players(game_day), list_goalkeepers(game_day))


def lineup_for(game_day: GameDay) -> Lineup | None:
    return Lineup.objects.filter(game_day=game_day).first()


def _locked_lineup(game_day: GameDay) -> Lineup:
    lineup = Lineup.objects.filter(game_day=game_day).first()
    if lineup is None:
        raise MatchdayError("Sorteie os times primeiro.")
    return lineup


def split_groups(grouping: Grouping, placement: Mapping[int, object]) -> list[Group]:
    """The groups whose players are not all in the same place (a team, or the goal).

    A player with no place yet is not split from anyone: the lineup cannot be confirmed
    until everyone has a place, and that is checked there.
    """
    return [
        group
        for group in grouping.groups
        if len({placement[pid] for pid in group.player_ids if pid in placement}) > 1
    ]


def _require_groups_whole(grouping: Grouping, placement: Mapping[int, object]) -> None:
    split = split_groups(grouping, placement)
    if split:
        groups = "; ".join(" + ".join(grouping.names_of(g.player_ids)) for g in split)
        raise MatchdayError(
            f"Estes jogadores precisam ficar no mesmo time: {groups}. "
            "Mova o grupo inteiro, ou deixe o estreante jogar em outro time (só neste domingo)."
        )


def team_capacity(line_players_count: int) -> int:
    """The most players a team holds when `line_players_count` line players are on the teams."""
    return max(team_sizes(line_players_count))


def _require_groups_fit(grouping: Grouping) -> None:
    if grouping.rules.allow_oversized_team:
        return
    capacity = team_capacity(len(grouping.line))
    too_big = [group for group in grouping.groups if group.size > capacity]
    if too_big:
        group = too_big[0]
        raise MatchdayError(
            f"O grupo {', '.join(grouping.names_of(group.player_ids))} tem {group.size} "
            f"jogadores e cada time tem {capacity} vagas. "
            "Escolha uma saída ao sortear de novo."
        )


def _require_goal_not_over_full(lineup: Lineup, assignments: Mapping[int, int | None]) -> None:
    """Line players go to the goal only beyond the 20 spots in the teams: with 20, nobody does."""
    before = lineup.spots.filter(team=None, player_id__in=assignments).count()
    after = sum(1 for number in assignments.values() if number is None)
    if goal_is_over_full(before=before, after=after, placed=len(assignments)):
        raise MatchdayError(goal_full_message(len(assignments)))


def _require_every_team_filled(lineup: Lineup, filled: set[int]) -> None:
    if any(team.pk not in filled for team in lineup.teams.all()):
        raise MatchdayError("Todo time precisa de pelo menos um jogador.")


def in_sync(lineup: Lineup) -> bool:
    """The lineup covers exactly the line players who are on the list right now, with the potes
    they had when the teams were made."""
    drawn = dict(lineup.spots.values_list("player_id", "pote"))
    playing = {player.pk: player.pote for player in line_players(lineup.game_day)}
    return drawn.keys() == playing.keys() and not repoted(drawn, playing)


def _same_players(lineup: Lineup) -> bool:
    drawn = set(lineup.spots.values_list("player_id", flat=True))
    return drawn == {player.pk for player in line_players(lineup.game_day)}


def repoted(drawn: Mapping[int, int | None], playing: Mapping[int, int]) -> list[int]:
    """Who is still on the list but changed pote since the teams were made (`drawn` and `playing`
    map player id to pote). Spots of teams made before the pote was kept have none to compare."""
    return [
        pid
        for pid, pote in drawn.items()
        if pid in playing and pote is not None and pote != playing[pid]
    ]


# Teams


@transaction.atomic
def draw(game_day_id: int, actor: Player, rng: random.Random | None = None) -> Lineup:
    """Draw (or redraw) the Sunday's teams from the line players on the list."""
    require_board(actor)
    game_day = lock_game_day(game_day_id)
    lineup = lineup_for(game_day)
    if lineup is not None and lineup.is_confirmed:
        raise MatchdayError("Os times já foram confirmados. Desfaça a confirmação para sortear.")

    grouping = grouping_of(game_day)
    pool = [DrawPlayer(p.pk, p.pote) for p in grouping.line]
    try:
        result = draw_teams(
            pool,
            rng or _system_random,
            groups=[group.player_ids for group in grouping.groups],
            allow_oversized=grouping.rules.allow_oversized_team,
        )
    except DrawConflict as conflict:
        raise RulesConflict(conflict.conflicts) from conflict
    except DrawError as error:
        raise MatchdayError(error.message) from error

    if lineup is None:
        lineup = Lineup.objects.create(game_day=game_day, drawn_by=actor, drawn_at=clock.now())
        Team.objects.bulk_create(
            Team(lineup=lineup, number=n, name=default_team_name(n))
            for n in range(1, TEAM_COUNT + 1)
        )
    else:
        lineup.drawn_at = clock.now()
        lineup.drawn_by = actor
        lineup.save(update_fields=["drawn_at", "drawn_by"])

    teams = {team.number: team for team in lineup.teams.all()}
    _replace_spots(
        lineup,
        {
            **{pid: teams[n] for n, members in enumerate(result.teams, start=1) for pid in members},
            **dict.fromkeys(result.in_goal),
        },
    )
    return lineup


def _replace_spots(lineup: Lineup, assignments: Mapping[int, Team | None]) -> None:
    potes = dict(Player.objects.filter(pk__in=assignments).values_list("pk", "pote"))
    lineup.spots.all().delete()
    LineupSpot.objects.bulk_create(
        LineupSpot(lineup=lineup, player_id=player_id, team=team, pote=potes[player_id])
        for player_id, team in assignments.items()
    )
    for match in lineup.matches.filter(roster_follows_lineup=True):
        _sync_roster(match)


@transaction.atomic
def assign(
    game_day_id: int,
    actor: Player,
    assignments: Mapping[int, int | None],
    *,
    leave_unplaced: bool = False,
) -> Lineup:
    """Adjust the teams by hand: player id -> team number, or None to play in goal.

    The assignments must cover exactly the line players on the list now, so a list that changed
    while the diretor was editing is never silently overwritten. With `leave_unplaced`, players
    who joined after the draw and have no place yet may be left out and stay unplaced.
    Players tied together (see `matchday.grouping`) must be placed together.
    """
    require_board(actor)
    game_day = lock_game_day(game_day_id)
    lineup = _locked_lineup(game_day)
    expected = {player.pk for player in line_players(game_day)}
    unplaced = expected - set(lineup.spots.values_list("player_id", flat=True))
    required = expected - unplaced if leave_unplaced else expected
    if not required <= set(assignments) <= expected:
        raise MatchdayError("A lista mudou enquanto você editava. Confira os times de novo.")
    teams = {team.number: team for team in lineup.teams.all()}
    if any(number is not None and number not in teams for number in assignments.values()):
        raise MatchdayError("Time inválido.")
    _require_groups_whole(grouping_of(game_day), assignments)
    _require_goal_not_over_full(lineup, assignments)
    spots = {pid: None if number is None else teams[number] for pid, number in assignments.items()}
    if lineup.is_confirmed:
        _require_every_team_filled(lineup, {team.pk for team in spots.values() if team is not None})
    _replace_spots(lineup, spots)
    return lineup


@transaction.atomic
def confirm(game_day_id: int, actor: Player) -> Lineup:
    """Publish the teams to the players."""
    require_board(actor)
    game_day = lock_game_day(game_day_id)
    lineup = _locked_lineup(game_day)
    if lineup.is_confirmed:
        raise MatchdayError("Os times já estão confirmados.")
    if not in_sync(lineup):
        changed = "A lista mudou" if not _same_players(lineup) else "Os potes mudaram"
        raise MatchdayError(
            f"{changed} depois do sorteio. Ajuste os times ou sorteie de novo antes de confirmar."
        )
    spots = dict(lineup.spots.values_list("player_id", "team_id"))
    grouping = grouping_of(game_day)
    _require_groups_whole(grouping, spots)
    _require_groups_fit(grouping)
    _require_every_team_filled(lineup, set(spots.values()))
    lineup.confirmed_at = clock.now()
    lineup.confirmed_by = actor
    lineup.save(update_fields=["confirmed_at", "confirmed_by"])
    return lineup


@transaction.atomic
def accept_pote_changes(game_day_id: int, actor: Player) -> Lineup:
    """Keep the teams as they are after players changed pote: the new potes are the baseline."""
    require_board(actor)
    game_day = lock_game_day(game_day_id)
    lineup = _locked_lineup(game_day)
    potes = {player.pk: player.pote for player in line_players(game_day)}
    changed = LineupSpot.objects.filter(lineup=lineup, player_id__in=potes)
    for spot in changed:
        if spot.pote != potes[spot.player_id]:
            spot.pote = potes[spot.player_id]
            spot.save(update_fields=["pote"])
    return lineup


@transaction.atomic
def unconfirm(game_day_id: int, actor: Player) -> Lineup:
    """Hide the teams from the players again, so they can be redrawn."""
    require_board(actor)
    game_day = lock_game_day(game_day_id)
    lineup = _locked_lineup(game_day)
    if not lineup.is_confirmed:
        raise MatchdayError("Os times ainda não foram confirmados.")
    lineup.confirmed_at = None
    lineup.confirmed_by = None
    lineup.save(update_fields=["confirmed_at", "confirmed_by"])
    return lineup


@transaction.atomic
def rename_teams(game_day_id: int, actor: Player, names: Mapping[int, str]) -> Lineup:
    """Name the teams (team number -> name). Names are required and must differ."""
    require_board(actor)
    game_day = lock_game_day(game_day_id)
    lineup = _locked_lineup(game_day)
    teams = {team.number: team for team in lineup.teams.all()}
    if set(names) != set(teams):
        raise MatchdayError("Informe o nome de todos os times.")
    cleaned = {number: " ".join(name.split()) for number, name in names.items()}
    if not all(cleaned.values()):
        raise MatchdayError("Todo time precisa de um nome.")
    if any(len(name) > TEAM_NAME_MAX_LENGTH for name in cleaned.values()):
        raise MatchdayError(f"Nome de time com no máximo {TEAM_NAME_MAX_LENGTH} caracteres.")
    if len({name.casefold() for name in cleaned.values()}) != len(cleaned):
        raise MatchdayError("Os times precisam de nomes diferentes.")
    # Two steps so swapping names between teams never trips the unique constraint.
    for team in teams.values():
        team.name = f"#{team.number}"
    Team.objects.bulk_update(teams.values(), ["name"])
    for number, team in teams.items():
        team.name = cleaned[number]
    Team.objects.bulk_update(teams.values(), ["name"])
    return lineup


# Matches


def _check_goalkeepers(pool: list[Player], home: int | None, away: int | None) -> None:
    allowed = {player.pk for player in pool}
    chosen = [pk for pk in (home, away) if pk is not None]
    if any(pk not in allowed for pk in chosen):
        raise MatchdayError("Escolha goleiros da lista ou jogadores de linha sorteados para o gol.")
    if len(chosen) == 2 and home == away:
        raise MatchdayError("Cada time precisa de um goleiro diferente.")


def _same_fixture(
    match: Match,
    home_team: Team,
    away_team: Team,
    home_goalkeeper: int | None,
    away_goalkeeper: int | None,
) -> bool:
    return (
        match.home_team_id == home_team.pk
        and match.away_team_id == away_team.pk
        and match.home_goalkeeper_id == home_goalkeeper
        and match.away_goalkeeper_id == away_goalkeeper
    )


def _create_match(
    lineup: Lineup,
    teams: Mapping[int, Team],
    order: int,
    *,
    home_team: int,
    away_team: int,
    home_goalkeeper: int | None,
    away_goalkeeper: int | None,
) -> Match:
    match = Match.objects.create(
        lineup=lineup,
        order=order,
        home_team=teams[home_team],
        away_team=teams[away_team],
        home_goalkeeper_id=home_goalkeeper,
        away_goalkeeper_id=away_goalkeeper,
        created_at=clock.now(),
    )
    _sync_roster(match)
    return match


def _last_order(lineup: Lineup) -> int:
    return lineup.matches.aggregate(last=Max("order"))["last"] or 0


@transaction.atomic
def add_match(
    game_day_id: int,
    actor: Player,
    *,
    home_team: int,
    away_team: int,
    home_goalkeeper: int | None = None,
    away_goalkeeper: int | None = None,
) -> Match:
    """Add a match to the end of the Sunday's table. Team arguments are team numbers."""
    require_board(actor)
    game_day = lock_game_day(game_day_id)
    lineup = _locked_lineup(game_day)
    teams = {team.number: team for team in lineup.teams.all()}
    if home_team not in teams or away_team not in teams:
        raise MatchdayError("Time inválido.")
    if home_team == away_team:
        raise MatchdayError("Escolha dois times diferentes.")
    _check_goalkeepers(goalkeeper_pool(lineup), home_goalkeeper, away_goalkeeper)
    last_match = lineup.matches.order_by("-order").first()
    if last_match is not None and _same_fixture(
        last_match, teams[home_team], teams[away_team], home_goalkeeper, away_goalkeeper
    ):
        # A double tap sends the same form twice; no real table repeats a match back to back.
        raise MatchdayError("Esta partida já é a última da tabela.")
    return _create_match(
        lineup,
        teams,
        (last_match.order if last_match else 0) + 1,
        home_team=home_team,
        away_team=away_team,
        home_goalkeeper=home_goalkeeper,
        away_goalkeeper=away_goalkeeper,
    )


def _played_matches(lineup: Lineup) -> list[PlayedMatch]:
    """The matches that ended, in table order, with their score and goalkeepers."""
    rows = (
        lineup.matches.filter(finished_at__isnull=False)
        .annotate(
            home_goals=Count("goals", filter=Q(goals__team_id=F("home_team_id"))),
            away_goals=Count("goals", filter=Q(goals__team_id=F("away_team_id"))),
        )
        .order_by("order", "pk")
    )
    return [
        PlayedMatch(
            row.home_team_id,
            row.away_team_id,
            row.home_goals,
            row.away_goals,
            row.home_goalkeeper_id,
            row.away_goalkeeper_id,
        )
        for row in rows
    ]


@dataclass(frozen=True)
class NextUp:
    """The match the waiting line calls next, and who waits behind it."""

    home: Team
    away: Team
    home_goalkeeper_id: int | None
    away_goalkeeper_id: int | None
    waiting: tuple[Team, ...]  # the teams left off court, the one who waited longest first


def next_up(lineup: Lineup) -> NextUp | None:
    """Who plays the next match: the winner stays, the longest-waiting team comes in, and after a
    draw the two teams who waited the longest. None while a match is still being played, and
    once the Sunday's game was finished."""
    if lineup.game_day.is_finished or lineup.matches.filter(finished_at=None).exists():
        return None
    teams = {team.pk: team for team in lineup.teams.all()}
    if len(teams) < 2:
        return None
    played = _played_matches(lineup)
    home, away = next_matchup(list(teams), played)
    keepers = next_goalkeepers(
        [player.pk for player in goalkeeper_pool(lineup)], played, (home, away)
    )
    line = [pk for pk in waiting_line(list(teams), played) if pk not in (home, away)]
    return NextUp(teams[home], teams[away], *keepers, tuple(teams[pk] for pk in line))


@transaction.atomic
def start_next_match(game_day_id: int, actor: Player) -> Match:
    """Call the next match of the Sunday from the waiting line, at the end of the table.

    The goalkeepers are suggested by who kept goal the longest ago; the match can be corrected
    afterwards.
    """
    require_board(actor)
    game_day = lock_game_day(game_day_id)
    lineup = _locked_lineup(game_day)
    if game_day.is_finished:
        raise MatchdayError("O jogo deste domingo já foi finalizado.")
    if lineup.matches.filter(finished_at=None).exists():
        raise MatchdayError("Encerre o jogo em andamento antes de chamar o próximo.")
    upcoming = next_up(lineup)
    if upcoming is None:
        raise MatchdayError("São precisos pelo menos dois times para jogar.")
    teams = {team.number: team for team in lineup.teams.all()}
    return _create_match(
        lineup,
        teams,
        _last_order(lineup) + 1,
        home_team=upcoming.home.number,
        away_team=upcoming.away.number,
        home_goalkeeper=upcoming.home_goalkeeper_id,
        away_goalkeeper=upcoming.away_goalkeeper_id,
    )


def _lock_match(match_id: int) -> Match:
    game_day_id = Match.objects.values_list("lineup__game_day_id", flat=True).get(pk=match_id)
    lock_game_day(game_day_id)
    return Match.objects.select_related("lineup__game_day", "home_team", "away_team").get(
        pk=match_id
    )


@transaction.atomic
def set_goalkeepers(match_id: int, actor: Player, *, home: int | None, away: int | None) -> Match:
    require_board(actor)
    match = _lock_match(match_id)
    _check_goalkeepers(match_goalkeeper_pool(match), home, away)
    match.home_goalkeeper_id = home
    match.away_goalkeeper_id = away
    match.save(update_fields=["home_goalkeeper", "away_goalkeeper"])
    match.players.filter(player_id__in=[pk for pk in (home, away) if pk is not None]).delete()
    if match.roster_follows_lineup:
        _sync_roster(match)
    return match


@transaction.atomic
def edit_match(match_id: int, actor: Player, *, home_team: int, away_team: int) -> Match:
    """Change the teams of a match (team numbers), even after it has goals.

    A team that stays in the match keeps its goals and players. When a team is replaced, its
    goals are credited to the team that takes its place, and the new team plays with its lineup.
    """
    require_board(actor)
    match = _lock_match(match_id)
    teams = {team.number: team for team in match.lineup.teams.all()}
    if home_team not in teams or away_team not in teams:
        raise MatchdayError("Time inválido.")
    if home_team == away_team:
        raise MatchdayError("Escolha dois times diferentes.")
    old = [match.home_team, match.away_team]
    new = [teams[home_team], teams[away_team]]
    removed = [team for team in old if team not in new]
    incoming = [team for team in new if team not in old]
    match.home_team, match.away_team = new
    match.save(update_fields=["home_team", "away_team"])
    for gone, replacement in zip(removed, incoming, strict=True):
        match.goals.filter(team=gone).update(team=replacement)
        match.players.filter(team=gone).delete()
        _fill_team(match, replacement)
    for goal in match.goals.all():
        _ensure_participant(match, goal.team, goal.scorer_id, goal.assist_id)
    return match


@transaction.atomic
def move_match(match_id: int, actor: Player, *, earlier: bool) -> Match:
    """Swap a match with its neighbour in the table."""
    require_board(actor)
    match = _lock_match(match_id)
    siblings = Match.objects.filter(lineup_id=match.lineup_id)
    if earlier:
        neighbour = siblings.filter(order__lt=match.order).order_by("-order").first()
    else:
        neighbour = siblings.filter(order__gt=match.order).order_by("order").first()
    if neighbour is None:
        raise MatchdayError("A partida já é a primeira." if earlier else "A partida já é a última.")
    # The order is unique per lineup, so park one of them on a free number while swapping.
    order, neighbour_order = match.order, neighbour.order
    Match.objects.filter(pk=match.pk).update(order=0)
    Match.objects.filter(pk=neighbour.pk).update(order=order)
    Match.objects.filter(pk=match.pk).update(order=neighbour_order)
    match.order = neighbour_order
    return match


@transaction.atomic
def delete_match(match_id: int, actor: Player) -> None:
    """Take a match off the table, with its goals."""
    require_board(actor)
    _lock_match(match_id).delete()


# Who played a match


def _keeper_ids(match: Match) -> set[int]:
    return {pk for pk in (match.home_goalkeeper_id, match.away_goalkeeper_id) if pk is not None}


def _fill_team(match: Match, team: Team) -> None:
    """Add the lineup's players of `team` who are not yet in the match and not keeping goal."""
    present = set(match.players.values_list("player_id", flat=True)) | _keeper_ids(match)
    spots = LineupSpot.objects.filter(lineup_id=match.lineup_id, team=team)
    MatchPlayer.objects.bulk_create(
        MatchPlayer(match=match, player_id=spot.player_id, team=team)
        for spot in spots
        if spot.player_id not in present
    )


def _sync_roster(match: Match) -> None:
    """Make the match's players the lineup's players of its two teams."""
    match.players.all().delete()
    _fill_team(match, match.home_team)
    _fill_team(match, match.away_team)


def _freeze_roster(match: Match) -> None:
    """Stop the match's players from following the lineup: from now on they are what was played."""
    if match.roster_follows_lineup:
        match.roster_follows_lineup = False
        match.save(update_fields=["roster_follows_lineup"])


def _ensure_participant(match: Match, team: Team, *player_ids: int | None) -> None:
    """Whoever scores or assists for a team played the match for it, unless already in it."""
    present = set(match.players.values_list("player_id", flat=True)) | _keeper_ids(match)
    missing = [pk for pk in dict.fromkeys(player_ids) if pk is not None and pk not in present]
    if missing:
        _freeze_roster(match)
        MatchPlayer.objects.bulk_create(
            MatchPlayer(match=match, player_id=pk, team=team) for pk in missing
        )


def match_roster(match: Match) -> dict[Side, list[Player]]:
    """The line players who played the match for each side, by name."""
    rows = match.players.select_related("player").order_by("player__name")
    return {
        Side.HOME: [row.player for row in rows if row.team_id == match.home_team_id],
        Side.AWAY: [row.player for row in rows if row.team_id == match.away_team_id],
    }


def roster_candidates(match: Match) -> list[Player]:
    """Who can be put in a match's roster: line players on the list and those already in it."""
    kept = [row.player for row in match.players.select_related("player")]
    keepers = _keeper_ids(match)
    everyone = {p.pk: p for p in [*kept, *line_players(match.lineup.game_day)]}
    return sorted(
        (p for p in everyone.values() if p.pk not in keepers), key=lambda p: p.name.casefold()
    )


@transaction.atomic
def set_roster(match_id: int, actor: Player, assignments: Mapping[int, Side | None]) -> Match:
    """Say who played the match for each side (player id -> side, or None to sit it out).

    This is how a substitution is recorded: a player covering for someone in the other team,
    or the match being played with fewer or more people than the lineup. Goalkeepers are set
    apart on the match itself.
    """
    require_board(actor)
    match = _lock_match(match_id)
    allowed = {player.pk for player in roster_candidates(match)}
    if any(pk not in allowed for pk in assignments):
        raise MatchdayError("Escolha jogadores de linha da lista do domingo.")
    teams = {Side.HOME: match.home_team, Side.AWAY: match.away_team}
    match.players.all().delete()
    MatchPlayer.objects.bulk_create(
        MatchPlayer(match=match, player_id=pk, team=teams[side])
        for pk, side in assignments.items()
        if side is not None
    )
    _freeze_roster(match)
    return match


def score_of(match: Match, *, without_goal: int | None = None) -> Score:
    goals = match.goals.exclude(pk=without_goal) if without_goal else match.goals.all()
    teams = list(goals.values_list("team_id", flat=True))
    return Score(teams.count(match.home_team_id), teams.count(match.away_team_id))


def side_players(match: Match, side: Side) -> list[Player]:
    """Who can score or assist for a side: anyone playing on the Sunday's list, since a player may
    sub in for another team. Who played the match for the side, goalkeeper included, comes first."""
    team_id = match.home_team_id if side == Side.HOME else match.away_team_id
    goalkeeper_id = match.home_goalkeeper_id if side == Side.HOME else match.away_goalkeeper_id
    on_side = {
        *match.players.filter(team_id=team_id).values_list("player_id", flat=True),
        goalkeeper_id,
    }
    game_day = match.lineup.game_day
    playing = [*line_players(game_day), *list_goalkeepers(game_day)]
    return sorted(playing, key=lambda p: (p.pk not in on_side, p.name.casefold()))


def side_scorers(match: Match, side: Side) -> list[Player]:
    """The line players who played the match for `side`, by name: the quick picks of a goal."""
    team_id = match.home_team_id if side == Side.HOME else match.away_team_id
    rows = match.players.filter(team_id=team_id).select_related("player")
    return sorted((row.player for row in rows), key=lambda p: p.name.casefold())


def _check_goal(
    match: Match,
    side: Side,
    *,
    scorer: int | None,
    assist: int | None,
    own_goal: bool,
    assist_not_applicable: bool,
    keep: Goal | None = None,
) -> None:
    """The scorer and assist must be on the list; those of the goal being edited stay valid."""
    if own_goal:
        if scorer is not None or assist is not None or assist_not_applicable:
            raise MatchdayError("Gol contra não tem autor nem assistência.")
        return
    if assist_not_applicable and assist is not None:
        raise MatchdayError("Escolha um jogador ou diga que a assistência não se aplica.")
    eligible = {player.pk for player in side_players(match, side)}
    if keep is not None:
        eligible |= {pk for pk in (keep.scorer_id, keep.assist_id) if pk is not None}
    if scorer not in eligible:
        raise MatchdayError("O autor do gol precisa estar na lista do domingo.")
    if assist is not None and assist not in eligible:
        raise MatchdayError("A assistência precisa ser de alguém da lista do domingo.")
    if assist == scorer:
        raise MatchdayError("O autor do gol não pode ser também a assistência.")


def _team_of(match: Match, side: Side) -> Team:
    return match.home_team if side == Side.HOME else match.away_team


def kickoff(game_day: GameDay) -> datetime:
    return schedule_for(game_day.date).starts


def has_started(game_day: GameDay) -> bool:
    """The Sunday's game kicked off: the same moment the attendance list opens."""
    return game_started(game_day)


def _require_started(match: Match) -> None:
    """Goals and full time count for the ranking, so they wait for the Sunday's kickoff."""
    if not has_started(match.lineup.game_day):
        raise MatchdayError("O jogo ainda não começou.")


@transaction.atomic
def add_goal(
    match_id: int,
    actor: Player,
    *,
    side: Side,
    scorer: int | None,
    assist: int | None = None,
    assist_not_applicable: bool = False,
    own_goal: bool = False,
) -> Goal:
    """Record a goal for `side`, also in a match already ended. An own goal has no scorer.

    The assist is a player, nobody (`assist` empty) or "does not apply". Reaching two goals ends
    the match.
    """
    require_board(actor)
    match = _lock_match(match_id)
    _require_started(match)
    try:
        after = score_of(match).add(side)
    except MatchOver as error:
        raise MatchdayError(error.message) from error
    _check_goal(
        match,
        side,
        scorer=scorer,
        assist=assist,
        own_goal=own_goal,
        assist_not_applicable=assist_not_applicable,
    )

    team = _team_of(match, side)
    goal = Goal.objects.create(
        match=match,
        team=team,
        scorer_id=scorer,
        assist_id=assist,
        assist_not_applicable=assist_not_applicable,
        own_goal=own_goal,
        created_at=clock.now(),
        created_by=actor,
    )
    _freeze_roster(match)
    _ensure_participant(match, team, scorer, assist)
    if after.decided and not match.is_finished:
        _finish(match, actor)
    return goal


@transaction.atomic
def edit_goal(
    goal_id: int,
    actor: Player,
    *,
    side: Side,
    scorer: int | None,
    assist: int | None = None,
    assist_not_applicable: bool = False,
    own_goal: bool = False,
) -> Goal:
    """Correct a goal: who scored or assisted, for which team, or whether it was an own goal."""
    require_board(actor)
    match_id = Goal.objects.values_list("match_id", flat=True).get(pk=goal_id)
    match = _lock_match(match_id)
    _require_started(match)
    goal = Goal.objects.get(pk=goal_id)
    before = score_of(match)
    try:
        after = score_of(match, without_goal=goal.pk).add(side)
    except MatchOver as error:
        raise MatchdayError(error.message) from error
    _check_goal(
        match,
        side,
        scorer=scorer,
        assist=assist,
        own_goal=own_goal,
        assist_not_applicable=assist_not_applicable,
        keep=goal,
    )

    goal.team = _team_of(match, side)
    goal.scorer_id = scorer
    goal.assist_id = assist
    goal.assist_not_applicable = assist_not_applicable
    goal.own_goal = own_goal
    goal.save(update_fields=["team", "scorer", "assist", "assist_not_applicable", "own_goal"])
    _freeze_roster(match)
    _ensure_participant(match, goal.team, scorer, assist)
    if after.decided and not match.is_finished:
        _finish(match, actor)
    else:
        _reopen_if_undecided(match, before, after)
    return goal


@transaction.atomic
def delete_goal(goal_id: int, actor: Player) -> bool:
    """Take a goal off the match. Returns whether that put the match back in play."""
    require_board(actor)
    match_id = Goal.objects.values_list("match_id", flat=True).get(pk=goal_id)
    match = _lock_match(match_id)
    before = score_of(match)
    Goal.objects.filter(pk=goal_id).delete()
    return _reopen_if_undecided(match, before, score_of(match))


def _reopen_if_undecided(match: Match, before: Score, after: Score) -> bool:
    """A match that ended on a decided score goes back to play once a correction undoes it.

    A match closed at full time keeps its result: only the score reaching the goal limit closes
    it by itself, so only undoing that reopens it.
    """
    if match.is_finished and before.decided and not after.decided:
        _reopen(match)
        return True
    return False


def _finish(match: Match, actor: Player) -> None:
    match.finished_at = clock.now()
    match.finished_by = actor
    match.roster_follows_lineup = False
    match.save(update_fields=["finished_at", "finished_by", "roster_follows_lineup"])


def _reopen(match: Match) -> None:
    match.finished_at = None
    match.finished_by = None
    match.save(update_fields=["finished_at", "finished_by"])


@transaction.atomic
def finish_match(match_id: int, actor: Player) -> Match:
    """End the match when time runs out. A draw stands."""
    require_board(actor)
    match = _lock_match(match_id)
    _require_started(match)
    if match.is_finished:
        raise MatchdayError("A partida já foi encerrada.")
    _finish(match, actor)
    return match


@transaction.atomic
def reopen_match(match_id: int, actor: Player) -> Match:
    """Put a finished match back to play. Its goals can be edited either way."""
    require_board(actor)
    match = _lock_match(match_id)
    if not match.is_finished:
        raise MatchdayError("A partida ainda não foi encerrada.")
    _reopen(match)
    return match
