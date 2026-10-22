from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from core import clock
from games.models import GameDay


class Lineup(models.Model):
    """The teams of one Sunday: drawn, adjusted by hand by the diretoria, then confirmed.

    Players only see the teams once they are confirmed.
    """

    game_day = models.OneToOneField(GameDay, on_delete=models.CASCADE, related_name="lineup")
    drawn_at = models.DateTimeField("sorteado em", default=clock.now)
    drawn_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="sorteado por",
        on_delete=models.PROTECT,
        related_name="+",
    )
    confirmed_at = models.DateTimeField("confirmado em", null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="confirmado por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "times do domingo"
        verbose_name_plural = "times dos domingos"

    def __str__(self) -> str:
        return f"Times de {self.game_day}"

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


class Team(models.Model):
    """One of the Sunday's teams. Kept across redraws, so its name and matches survive."""

    lineup = models.ForeignKey(Lineup, on_delete=models.CASCADE, related_name="teams")
    number = models.PositiveSmallIntegerField("número")
    name = models.CharField("nome", max_length=30)

    class Meta:
        verbose_name = "time"
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["lineup", "number"], name="team_number_per_lineup"),
            models.UniqueConstraint("lineup", Lower("name"), name="team_name_per_lineup"),
            models.CheckConstraint(condition=models.Q(number__gte=1), name="team_number_min"),
        ]

    def __str__(self) -> str:
        return self.name


class LineupSpot(models.Model):
    """Where a line player plays on the Sunday: in a team, or in goal (`team` empty)."""

    lineup = models.ForeignKey(Lineup, on_delete=models.CASCADE, related_name="spots")
    player = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="lineup_spots"
    )
    team = models.ForeignKey(
        Team, on_delete=models.CASCADE, null=True, blank=True, related_name="spots"
    )
    pote = models.PositiveSmallIntegerField(
        "pote no sorteio",
        null=True,
        blank=True,
        help_text="O pote do jogador quando os times foram feitos; vazio nos times antigos.",
    )

    class Meta:
        verbose_name = "jogador no time"
        verbose_name_plural = "jogadores nos times"
        constraints = [
            models.UniqueConstraint(fields=["lineup", "player"], name="one_spot_per_lineup"),
        ]

    def __str__(self) -> str:
        return f"{self.player} em {self.team or 'gol'}"

    @property
    def in_goal(self) -> bool:
        return self.team_id is None


class Match(models.Model):
    """A match of the Sunday's table. Its score is the count of its goals per team."""

    lineup = models.ForeignKey(Lineup, on_delete=models.CASCADE, related_name="matches")
    order = models.PositiveSmallIntegerField("ordem")
    home_team = models.ForeignKey(
        Team, verbose_name="time 1", on_delete=models.RESTRICT, related_name="+"
    )
    away_team = models.ForeignKey(
        Team, verbose_name="time 2", on_delete=models.RESTRICT, related_name="+"
    )
    home_goalkeeper = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="goleiro do time 1",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    away_goalkeeper = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="goleiro do time 2",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    finished_at = models.DateTimeField("encerrada em", null=True, blank=True)
    finished_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="encerrada por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    roster_follows_lineup = models.BooleanField(
        "escalação acompanha os times",
        default=True,
        help_text=(
            "Enquanto verdadeiro, quem joga a partida é recalculado a cada ajuste dos times. "
            "Editar a escalação à mão ou encerrar a partida congela o que foi jogado."
        ),
    )
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        verbose_name = "partida"
        ordering = ["order", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["lineup", "order"], name="match_order_per_lineup"),
            models.CheckConstraint(
                condition=~models.Q(home_team=models.F("away_team")),
                name="match_teams_differ",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(home_goalkeeper=None)
                    | models.Q(away_goalkeeper=None)
                    | ~models.Q(home_goalkeeper=models.F("away_goalkeeper"))
                ),
                name="match_goalkeepers_differ",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.home_team} x {self.away_team}"

    @property
    def is_finished(self) -> bool:
        return self.finished_at is not None


class MatchPlayer(models.Model):
    """Who played a match for a team, besides its goalkeepers (kept on the match itself).

    This is what the ranking counts for wins: moving a player between teams on the Sunday's
    lineup never rewrites the matches already played.
    """

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="players")
    player = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="match_participations"
    )
    team = models.ForeignKey(Team, on_delete=models.RESTRICT, related_name="+")

    class Meta:
        verbose_name = "jogador na partida"
        verbose_name_plural = "jogadores nas partidas"
        constraints = [
            models.UniqueConstraint(fields=["match", "player"], name="one_side_per_match_player"),
        ]

    def __str__(self) -> str:
        return f"{self.player} em {self.team} ({self.match})"


class Goal(models.Model):
    """A goal for `team`. An own goal has no scorer and no assist.

    The assist is answered for every goal that has a scorer: a player, nobody (`assist` empty)
    or "does not apply" (`assist_not_applicable`, e.g. a penalty), which is not the same thing.
    """

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="goals")
    team = models.ForeignKey(Team, on_delete=models.RESTRICT, related_name="+")
    scorer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="autor",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="goals",
    )
    assist = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="assistência",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assists",
    )
    assist_not_applicable = models.BooleanField("assistência não se aplica", default=False)
    own_goal = models.BooleanField("gol contra", default=False)
    created_at = models.DateTimeField(default=clock.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        verbose_name = "gol"
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(own_goal=True, scorer=None, assist=None)
                    | models.Q(own_goal=False, scorer__isnull=False)
                ),
                name="goal_has_scorer_unless_own_goal",
            ),
            models.CheckConstraint(
                condition=models.Q(assist=None) | ~models.Q(assist=models.F("scorer")),
                name="goal_assist_is_not_the_scorer",
            ),
            models.CheckConstraint(
                condition=models.Q(assist_not_applicable=False) | models.Q(assist=None),
                name="goal_assist_not_applicable_has_no_assist",
            ),
        ]

    def __str__(self) -> str:
        return f"Gol de {self.scorer or 'contra'} ({self.match})"


class TeamRules(models.Model):
    """The diretoria's rules for drawing one Sunday's teams. Created on first use."""

    game_day = models.OneToOneField(GameDay, on_delete=models.CASCADE, related_name="team_rules")
    rookies_follow_inviter = models.BooleanField(
        "estreantes ficam com quem convidou",
        default=True,
        help_text=(
            "Quem joga o primeiro Babinha fica no time de quem o convidou, se este também joga."
        ),
    )
    allow_oversized_team = models.BooleanField(
        "aceitar time com mais jogadores",
        default=False,
        help_text="A diretoria aceitou um time maior que os outros para manter um grupo junto.",
    )

    class Meta:
        verbose_name = "regras dos times"
        verbose_name_plural = "regras dos times"

    def __str__(self) -> str:
        return f"Regras dos times de {self.game_day}"


class TogetherGroup(models.Model):
    """Line players the diretoria wants in the same team on one Sunday (e.g. who share a ride)."""

    game_day = models.ForeignKey(GameDay, on_delete=models.CASCADE, related_name="together_groups")
    name = models.CharField("nome", max_length=40, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        verbose_name = "grupo que joga junto"
        verbose_name_plural = "grupos que jogam juntos"
        ordering = ["pk"]

    def __str__(self) -> str:
        return self.name or f"Grupo {self.pk}"


class TogetherMember(models.Model):
    """A player in a `TogetherGroup`. A player is in at most one group per Sunday.

    `game_day` repeats the group's Sunday so the database can enforce that.
    """

    group = models.ForeignKey(TogetherGroup, on_delete=models.CASCADE, related_name="members")
    game_day = models.ForeignKey(GameDay, on_delete=models.CASCADE, related_name="+")
    player = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="together_memberships"
    )

    class Meta:
        verbose_name = "membro do grupo"
        verbose_name_plural = "membros do grupo"
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["game_day", "player"], name="one_together_group_per_player_and_day"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.player} em {self.group}"


class RookieRelease(models.Model):
    """The diretoria let a first-timer play apart from who invited them, on one Sunday only."""

    game_day = models.ForeignKey(GameDay, on_delete=models.CASCADE, related_name="rookie_releases")
    player = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="rookie_releases"
    )
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    released_at = models.DateTimeField(default=clock.now)

    class Meta:
        verbose_name = "estreante liberado"
        verbose_name_plural = "estreantes liberados"
        constraints = [
            models.UniqueConstraint(
                fields=["game_day", "player"], name="one_release_per_player_day"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.player} liberado em {self.game_day}"
