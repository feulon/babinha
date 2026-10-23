"""Forms of the team assembly: the groups the diretoria makes by hand."""

from collections.abc import Sequence

from django import forms

from accounts.models import Player
from matchday.team_rules import GROUP_NAME_MAX_LENGTH, MAX_GROUP_SIZE


class GroupForm(forms.Form):
    """Pick two to five line players who play in the same team."""

    players = forms.MultipleChoiceField(
        label="Jogadores",
        widget=forms.CheckboxSelectMultiple,
        error_messages={
            "required": f"Escolha de 2 a {MAX_GROUP_SIZE} jogadores para o grupo.",
            "invalid_choice": "Só jogadores de linha da lista, que ainda não estão num grupo.",
        },
    )
    name = forms.CharField(
        label="Nome do grupo (opcional)",
        max_length=GROUP_NAME_MAX_LENGTH,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Nome (opcional) · ex.: Mesmo carro"}),
    )

    def __init__(self, *args: object, candidates: Sequence[Player], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["players"].choices = [
            (str(player.pk), player.board_label) for player in candidates
        ]

    def clean_players(self) -> list[int]:
        ids = [int(value) for value in self.cleaned_data["players"]]
        if not 2 <= len(ids) <= MAX_GROUP_SIZE:
            raise forms.ValidationError(f"Escolha de 2 a {MAX_GROUP_SIZE} jogadores para o grupo.")
        return ids
