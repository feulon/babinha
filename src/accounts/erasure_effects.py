"""What erasing a player changes in what stays: scores, money, ranking and Bola de Ouro.

Read-only: it is shown before the erasure (and in the dry-run) so the operator knows what the
rest of the group will see differ once the player is gone.
"""

from collections.abc import Iterable

from django.db.models import Prefetch, Sum

from accounts.models import Player
from community import selectors as community
from community.domain import voting
from community.models import PlaceTally
from core.templatetags.money import brl
from finance.models import OPEN_STATUSES, Charge, ChargeStatus
from games.models import GameDay
from matchday.models import Goal, Match


def effects(player: Player) -> list[str]:
    return [
        *_scores(player),
        *_money(player),
        *_ranking(player),
        *_gold_balls(player),
    ]


def _sunday(game_day: GameDay) -> str:
    return f"Domingo {game_day.date:%d/%m/%Y}"


def _score(match: Match, goals: Iterable[Goal]) -> str:
    goals = list(goals)
    home = sum(1 for goal in goals if goal.team_id == match.home_team_id)
    return f"{home} x {len(goals) - home}"


def _scores(player: Player) -> list[str]:
    matches = (
        Match.objects.filter(goals__scorer=player)
        .distinct()
        .select_related("lineup__game_day", "home_team", "away_team")
        .prefetch_related(Prefetch("goals", queryset=Goal.objects.all()))
        .order_by("lineup__game_day__date", "order")
    )
    lines = []
    for match in matches:
        goals = list(match.goals.all())
        after = [goal for goal in goals if goal.scorer_id != player.pk]
        lines.append(
            f"Placar: {_sunday(match.lineup.game_day)}, {match.home_team} x {match.away_team}: "
            f"{_score(match, goals)} passa a {_score(match, after)}."
        )
    return lines


def _money(player: Player) -> list[str]:
    charges = Charge.objects.filter(player=player)
    paid = charges.filter(status=ChargeStatus.PAID).aggregate(total=Sum("amount"))["total"]
    owed = charges.filter(status__in=OPEN_STATUSES).aggregate(total=Sum("amount"))["total"]
    lines = []
    if paid:
        lines.append(
            f"Caixa: {brl(paid)} que ele pagou saem dos recebidos nos relatórios dos meses."
        )
    if owed:
        lines.append(f"Cobranças: {brl(owed)} em aberto deixam de ser cobrados.")
    return lines


def _ranking(player: Player) -> list[str]:
    goals = Goal.objects.filter(scorer=player).count()
    assists = Goal.objects.filter(assist=player).count()
    assists_on_his_goals = Goal.objects.filter(scorer=player, assist__isnull=False).count()
    played = player.match_participations.count()
    if not (goals or assists or played):
        return []
    lines = [
        f"Ranking: ele sai da tabela ({played} partidas, {goals} gols e {assists} assistências "
        "deixam de contar)."
    ]
    if assists_on_his_goals:
        lines.append(
            f"Ranking: {assists_on_his_goals} assistências de outros jogadores em gols dele "
            "deixam de contar."
        )
    return lines


def _gold_balls(player: Player) -> list[str]:
    days = GameDay.objects.filter(
        pk__in=PlaceTally.objects.filter(nominee=player, votes__gt=0).values("game_day")
    ).order_by("date")
    standings = community.closed_standings(days)
    names = dict(Player.objects.values_list("pk", "name"))
    lines = []
    for day in days:
        before = [row.player_id for row in voting.winners(standings.get(day.pk, ()))]
        if player.pk not in before:
            continue
        rest = [row for row in standings[day.pk] if row.player_id != player.pk]
        after = [names[row.player_id] for row in voting.winners(rest)]
        held = "era dele" if len(before) == 1 else "era dividida com ele"
        then = f"fica com {', '.join(after)}" if after else "fica sem ganhador"
        lines.append(f"Bola de Ouro: {_sunday(day)} {held}; {then}.")
    return lines
