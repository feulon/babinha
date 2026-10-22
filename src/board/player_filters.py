"""Filters of the diretoria's player list, kept in the querystring so the screen is linkable."""

from urllib.parse import urlencode

from django import forms
from django.db.models import QuerySet

from accounts.models import POTE_MAX, POTE_MIN, Player, PlayerType, Position

POTES = [(str(n), f"Pote {n}") for n in range(POTE_MIN, POTE_MAX + 1)]


class PlayerFilterForm(forms.Form):
    """Every field is optional and a value that makes no sense is simply ignored."""

    q = forms.CharField(label="Buscar por nome", required=False, max_length=60)
    tipo = forms.ChoiceField(
        label="Tipo", required=False, choices=[("", "Todos"), *PlayerType.choices]
    )
    posicao = forms.ChoiceField(
        label="Posição", required=False, choices=[("", "Todas"), *Position.choices]
    )
    pote = forms.ChoiceField(label="Pote", required=False, choices=[("", "Todos"), *POTES])
    sem_indicador = forms.BooleanField(label="Sem indicador", required=False)
    inativos = forms.BooleanField(label="Só desativados", required=False)

    def values(self) -> dict[str, str]:
        """The filters that are on, cleaned; invalid ones count as off."""
        self.is_valid()
        data = {name: self.cleaned_data[name] for name in self.cleaned_data}
        data["q"] = data.get("q", "").strip()
        for flag in ("sem_indicador", "inativos"):
            if data.get(flag):
                data[flag] = "1"
        return {name: value for name, value in data.items() if value}

    def querystring(self) -> str:
        return urlencode(self.values())

    def apply(self, players: QuerySet[Player]) -> QuerySet[Player]:
        values = self.values()
        players = players.filter(is_active="inativos" not in values)
        if "q" in values:
            players = players.filter(name__icontains=values["q"])
        if "tipo" in values:
            players = players.of_type(values["tipo"])
        if "posicao" in values:
            players = players.filter(position=values["posicao"])
        if "pote" in values:
            players = players.filter(position=Position.LINHA, pote=int(values["pote"]))
        if "sem_indicador" in values:
            players = players.filter(referred_by__isnull=True)
        return players
