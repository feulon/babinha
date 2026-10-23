"""Forms of the game day screens: teams, match table and results."""

from collections.abc import Sequence

from django import forms

from accounts.models import Player
from matchday.domain.score import Side
from matchday.models import Goal, Team
from matchday.services import TEAM_NAME_MAX_LENGTH

OWN_GOAL = "contra"
ASSIST_NOT_APPLICABLE = "na"
NO_ASSIST_LABEL = "Nenhum"
NOT_APPLICABLE_LABEL = "Não se aplica"


def _player_choices(players: Sequence[Player], empty: str) -> list[tuple[str, str]]:
    return [("", empty), *((str(p.pk), p.board_label) for p in players)]


def _optional_id(value: str) -> int | None:
    return int(value) if value else None


class TeamNamesForm(forms.Form):
    def __init__(self, *args: object, teams: Sequence[Team], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        for team in teams:
            self.fields[f"t{team.number}"] = forms.CharField(
                label=f"Time {team.number}",
                max_length=TEAM_NAME_MAX_LENGTH,
                initial=team.name,
            )
        self.teams = tuple(teams)

    def names(self) -> dict[int, str]:
        return {team.number: self.cleaned_data[f"t{team.number}"] for team in self.teams}


class GoalkeepersForm(forms.Form):
    home_goalkeeper = forms.ChoiceField(label="Goleiro", required=False)
    away_goalkeeper = forms.ChoiceField(label="Goleiro", required=False)

    def __init__(self, *args: object, pool: Sequence[Player], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        for name in ("home_goalkeeper", "away_goalkeeper"):
            self.fields[name].choices = _player_choices(pool, "A definir")

    def goalkeepers(self) -> tuple[int | None, int | None]:
        return (
            _optional_id(self.cleaned_data["home_goalkeeper"]),
            _optional_id(self.cleaned_data["away_goalkeeper"]),
        )


class MatchTeamsForm(forms.Form):
    """The two teams of a match, to correct it after it was added to the table."""

    home_team = forms.ChoiceField(label="Time 1")
    away_team = forms.ChoiceField(label="Time 2")

    def __init__(self, *args: object, teams: Sequence[Team], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        choices = [(str(team.number), team.name) for team in teams]
        self.fields["home_team"].choices = choices
        self.fields["away_team"].choices = choices

    def clean(self) -> dict:
        cleaned = super().clean()
        home, away = cleaned.get("home_team"), cleaned.get("away_team")
        if home and home == away:
            raise forms.ValidationError("Escolha dois times diferentes.")
        return cleaned

    def teams(self) -> tuple[int, int]:
        return int(self.cleaned_data["home_team"]), int(self.cleaned_data["away_team"])


class RosterForm(forms.Form):
    """One select per line player: which side they played the match for, or neither."""

    def __init__(
        self,
        *args: object,
        players: Sequence[Player],
        home: Team,
        away: Team,
        current: dict[int, Side],
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        choices = [("", "Não jogou"), (Side.HOME.value, home.name), (Side.AWAY.value, away.name)]
        for player in players:
            self.fields[self.field_name(player)] = forms.ChoiceField(
                label=player.display_name,
                choices=choices,
                required=False,
                initial=current[player.pk].value if player.pk in current else "",
                widget=forms.Select(
                    attrs={"class": "select-inline", "aria-label": f"Time de {player.display_name}"}
                ),
            )
        self.players = tuple(players)

    @staticmethod
    def field_name(player: Player) -> str:
        return f"p{player.pk}"

    def assignments(self) -> dict[int, Side | None]:
        values = {p.pk: self.cleaned_data[self.field_name(p)] for p in self.players}
        return {pk: Side(value) if value else None for pk, value in values.items()}


class GoalForm(forms.Form):
    scorer = forms.ChoiceField(label="Autor do gol")
    assist = forms.ChoiceField(label="Assistência", required=False)

    def __init__(
        self, *args: object, players: Sequence[Player], opponent: str, **kwargs: object
    ) -> None:
        super().__init__(*args, **kwargs)
        self.fields["scorer"].choices = [
            *_player_choices(players, "Quem marcou?"),
            (OWN_GOAL, f"Gol contra ({opponent})"),
        ]
        self.fields["assist"].choices = [
            *_player_choices(players, NO_ASSIST_LABEL),
            (ASSIST_NOT_APPLICABLE, NOT_APPLICABLE_LABEL),
        ]

    def clean(self) -> dict:
        cleaned = super().clean()
        scorer, assist = cleaned.get("scorer"), cleaned.get("assist")
        if scorer == OWN_GOAL and assist:
            raise forms.ValidationError("Gol contra não tem assistência.")
        if scorer and scorer == assist:
            raise forms.ValidationError("O autor do gol não pode ser também a assistência.")
        return cleaned

    @property
    def own_goal(self) -> bool:
        return self.cleaned_data["scorer"] == OWN_GOAL

    def scorer_id(self) -> int | None:
        return None if self.own_goal else int(self.cleaned_data["scorer"])

    @property
    def assist_not_applicable(self) -> bool:
        return self.cleaned_data["assist"] == ASSIST_NOT_APPLICABLE

    def assist_id(self) -> int | None:
        value = self.cleaned_data["assist"]
        return None if value == ASSIST_NOT_APPLICABLE else _optional_id(value)


class GoalEditForm(GoalForm):
    """Correct a recorded goal: the team it counts for, who scored and who assisted."""

    side = forms.ChoiceField(label="Gol para o time")

    field_order = ["side", "scorer", "assist"]

    def __init__(self, *args: object, home: Team, away: Team, goal: Goal, **kwargs: object) -> None:
        super().__init__(*args, opponent="do adversário", **kwargs)
        self.fields["side"].choices = [(Side.HOME.value, home.name), (Side.AWAY.value, away.name)]
        self.initial = {
            "side": (Side.HOME if goal.team_id == home.pk else Side.AWAY).value,
            "scorer": OWN_GOAL if goal.own_goal else str(goal.scorer_id),
            "assist": ASSIST_NOT_APPLICABLE
            if goal.assist_not_applicable
            else str(goal.assist_id or ""),
        }

    def side_value(self) -> Side:
        return Side(self.cleaned_data["side"])
