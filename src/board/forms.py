from datetime import date

from django import forms
from django.db.models import Q

from accounts.forms import (
    ContactCleanMixin,
    ContactEmailField,
    PhoneField,
    PhotoField,
    PhotoFormMixin,
    ReferrerField,
)
from accounts.models import Player
from accounts.phone import format_phone
from core import clock
from core.forms import localize_money
from core.models import DUE_DAY_MAX, DUE_DAY_MIN, FINE_FACTOR_MIN, ClubSettings
from core.pix import normalize_pix_key
from games.domain.roster import Section
from games.domain.types import EntryStatus, Position

POTE_HELP = (
    "Pote 1 é o mais forte e pote 5 o mais fraco. O sorteio espalha os potes entre os times."
)


MONTHLY_LABEL = "Mensalista deste mês"
MONTHLY_HELP = "Vale só até o fim do mês e não pode ser retirada."


def monthly_field() -> forms.BooleanField:
    """The diretoria's way to join a player in this month's plan."""
    return forms.BooleanField(required=False, label=MONTHLY_LABEL, help_text=MONTHLY_HELP)


def place_after(form: forms.Form, name: str, after: str) -> None:
    """Show the field `name` right after the field `after`."""
    order = [key for key in form.fields if key != name]
    order.insert(order.index(after) + 1, name)
    form.order_fields(order)


class SameNameMixin:
    """Two players with the same nickname are hard to tell apart on the lists and the teams: ask
    to confirm before saving a nickname somebody already has. The box only exists once it is
    needed."""

    instance: Player

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["same_name_ok"] = forms.BooleanField(
            required=False, label="Salvar assim mesmo, são pessoas diferentes"
        )
        if not (
            self.is_bound and self._nickname_is_taken(self.data.get(self.add_prefix("nickname")))
        ):
            del self.fields["same_name_ok"]

    def _nickname_is_taken(self, raw: str | None) -> bool:
        nickname = " ".join((raw or "").split())
        if not nickname or (self.instance.pk and nickname == self.instance.nickname):
            return False
        return (
            Player.objects.filter(nickname__iexact=nickname).exclude(pk=self.instance.pk).exists()
        )

    def player_data(self) -> dict:
        """The cleaned values that are player fields (not the confirmation box)."""
        return {
            name: value
            for name, value in self.cleaned_data.items()
            if name not in {"same_name_ok", "photo", "remove_photo", "mensalista"}
        }

    def clean_name(self) -> str:
        name = " ".join(self.cleaned_data["name"].split())
        if not name:
            raise forms.ValidationError("Informe o nome.")
        return name

    def clean_nickname(self) -> str:
        nickname = " ".join(self.cleaned_data["nickname"].split())
        if not nickname:
            raise forms.ValidationError("Informe o apelido.")
        if self._nickname_is_taken(nickname) and not self.data.get(self.add_prefix("same_name_ok")):
            raise forms.ValidationError(
                f"Já existe um jogador com o apelido {nickname}. Use outro apelido para "
                "diferenciar (por exemplo, Careca da Zaga) ou marque a opção abaixo para "
                "salvar assim mesmo."
            )
        return nickname


class PlayerForm(PhotoFormMixin, SameNameMixin, ContactCleanMixin, forms.ModelForm):
    photo = PhotoField(
        label="Foto (opcional)",
    )
    remove_photo = forms.BooleanField(required=False, label="Remover a foto atual")
    field_order = ["name", "nickname", "photo", "remove_photo"]
    # Optional only for players from before the phone existed, who have none to keep.
    phone = PhoneField(required=False)
    # Optional only for players from before the e-mail was required.
    email = ContactEmailField(
        required=False,
        help_text="Sem e-mail o jogador não recebe o link de nova senha: use “Nova senha”.",
    )
    referred_by = ReferrerField(
        label="Indicado por",
        queryset=Player.objects.all(),
        empty_label="Escolha um jogador",
        help_text="Contas criadas pela diretoria guardam quem as criou.",
    )
    mensalista = monthly_field()

    class Meta:
        model = Player
        fields = [
            "name",
            "nickname",
            "phone",
            "email",
            "player_type",
            "position",
            "pote",
            "is_diretoria",
            "is_active",
            "referred_by",
            "member_since",
        ]
        widgets = {
            "pote": forms.Select(choices=[(i, f"Pote {i}") for i in range(1, 6)]),
            "member_since": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        labels = {
            "is_diretoria": "Faz parte da diretoria",
            "is_active": "Ativo",
        }
        help_texts = {
            "pote": POTE_HELP,
            "is_active": (
                "Desmarque para quem saiu do Babinha: some das listas e não entra mais. "
                "O histórico fica."
            ),
        }

    def __init__(self, *args: object, editor: Player, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        place_after(self, "mensalista", "player_type")
        self.initial["mensalista"] = self.instance.pk is not None and self.instance.is_mensalista
        if self.instance.is_presidente or (editor is not None and editor.pk == self.instance.pk):
            # Nobody switches themselves off, and the presidente is always there.
            self.fields["is_active"].disabled = True
        if not self.instance.photo:
            del self.fields["remove_photo"]
        if self.instance.phone:
            self.fields["phone"].required = True
        else:
            self.fields[
                "phone"
            ].help_text = "Ainda sem celular: ele será pedido ao jogador no próximo acesso."
        self.fields["referred_by"].queryset = Player.objects.filter(
            Q(is_active=True) | Q(pk=self.instance.referred_by_id)
        ).exclude(pk=self.instance.pk)
        if self.instance.is_presidente:
            # The presidente always has diretoria powers and is the only one nobody referred.
            self.fields["is_diretoria"].disabled = True
            self.fields["referred_by"].disabled = True
        if self.instance.is_presidente or self.instance.referred_by_id is None:
            self.fields["referred_by"].required = False
        if not editor.is_presidente:
            self._lock_presidente_only_fields()

    def save(self, commit: bool = True) -> Player:
        if "player_type" in self.changed_data:
            self.instance.player_type_set_at = clock.now()
        return super().save(commit)

    def _lock_presidente_only_fields(self) -> None:
        """The presidente's own contact and type, and who is on the diretoria, are the
        presidente's to change: anyone else on the diretoria sees them but cannot edit."""
        locked = ["is_diretoria"]
        if self.instance.is_presidente:
            locked += ["phone", "email", "player_type", "mensalista"]
        for name in locked:
            field = self.fields[name]
            field.disabled = True
            hint = "Só o presidente altera."
            field.help_text = f"{field.help_text} {hint}".strip()

    def clean_mensalista(self) -> bool:
        enrolled = self.cleaned_data["mensalista"]
        if self.instance.pk and self.instance.is_mensalista and not enrolled:
            raise forms.ValidationError("A adesão vale até o fim do mês e não pode ser retirada.")
        return enrolled

    def clean_referred_by(self) -> Player | None:
        referrer = self.cleaned_data["referred_by"]
        seen = set()
        ancestor = referrer
        while ancestor is not None and ancestor.pk not in seen:
            if ancestor.pk == self.instance.pk:
                raise forms.ValidationError(
                    "Essa pessoa veio por indicação deste jogador. Escolha outra."
                )
            seen.add(ancestor.pk)
            ancestor = ancestor.referred_by
        return referrer

    def clean_member_since(self) -> date | None:
        since = self.cleaned_data["member_since"]
        if since is not None and since > clock.now().date():
            raise forms.ValidationError("A data de entrada não pode ser no futuro.")
        return since


MONEY_FIELDS = ("monthly_fee", "single_fee", "fine_base", "late_fine_15", "late_fine_30")


class ClubSettingsForm(forms.ModelForm):
    board_contact_phone = PhoneField(
        required=False,
        label="WhatsApp da diretoria (opcional)",
    )

    class Meta:
        model = ClubSettings
        fields = [
            *MONEY_FIELDS,
            "fine_factor",
            "monthly_due_day",
            "avulso_after_games",
            "pix_key",
            "board_contact_phone",
            "venue_name",
        ]
        widgets = {"pix_key": forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"})}

    def __init__(self, *args: object, editor: Player, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        if not editor.is_presidente:
            # The key receives everyone's monthly fee: only the presidente, whose it is, changes it.
            pix_key = self.fields["pix_key"]
            pix_key.disabled = True
            pix_key.help_text = "Só o presidente altera a chave Pix."
        for name in MONEY_FIELDS:
            localize_money(self.fields[name])
        self.fields["fine_base"].label = "Primeira falta (R$)"
        self.fields["late_fine_15"].label = "Atraso de 15 min (R$)"
        self.fields["late_fine_30"].label = "Atraso de 30 min (R$)"
        factor = self.fields["fine_factor"]
        localize_money(
            factor,
            minimum=FINE_FACTOR_MIN,
            below_minimum="O multiplicador não pode ser menor que 1,00.",
        )
        factor.widget.attrs["placeholder"] = "1,50"
        factor.error_messages.update(
            invalid="Informe um multiplicador, por exemplo 1,50.",
            max_digits="O multiplicador é grande demais.",
            max_whole_digits="O multiplicador é grande demais.",
        )
        self.fields["avulso_after_games"].label = "Futebóis para virar avulso"
        self.initial["board_contact_phone"] = format_phone(self.instance.board_contact_phone)
        self.fields["monthly_due_day"].widget = forms.Select(
            choices=[(day, f"Dia {day}") for day in range(DUE_DAY_MIN, DUE_DAY_MAX + 1)]
        )

    def clean_pix_key(self) -> str:
        raw = self.cleaned_data["pix_key"]
        return normalize_pix_key(raw) if raw.strip() else ""


class AddToListForm(forms.Form):
    player = forms.ModelChoiceField(
        label="Jogador", queryset=Player.objects.filter(is_active=True), empty_label="Escolha"
    )

    def __init__(self, *args: object, exclude_ids: set[int] = frozenset(), **kwargs: object):
        super().__init__(*args, **kwargs)
        self.fields["player"].queryset = Player.objects.filter(is_active=True).exclude(
            pk__in=exclude_ids
        )
        self.fields["player"].label_from_instance = lambda p: p.board_label


SECTIONS = {
    f"{status}-{position}": Section(status, position)
    for status in (EntryStatus.LISTA, EntryStatus.ESPERA)
    for position in Position
}


class PlaceOnListForm(forms.Form):
    """Where the diretoria puts a player: a section ("lista-linha", "espera-goleiro", ...) and,
    optionally, a place in it (1 is the top)."""

    section = forms.ChoiceField(choices=[(key, key) for key in SECTIONS])
    index = forms.IntegerField(label="Posição", min_value=1, max_value=99, required=False)

    def clean_section(self) -> Section:
        return SECTIONS[self.cleaned_data["section"]]


class NewPlayerForm(PhotoFormMixin, SameNameMixin, ContactCleanMixin, forms.ModelForm):
    """Registers a new player from the Jogadores tab; the login is handed over by the diretoria.
    The player is not put on any Sunday's list."""

    phone = PhoneField()
    email = ContactEmailField()
    mensalista = monthly_field()
    photo = PhotoField(
        label="Foto (opcional)",
    )
    field_order = ["name", "nickname", "photo"]

    class Meta:
        model = Player
        fields = [
            "name",
            "nickname",
            "phone",
            "email",
            "player_type",
            "position",
            "pote",
            "member_since",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "off"}),
            "pote": forms.Select(choices=[(i, f"Pote {i}") for i in range(1, 6)]),
            "member_since": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        labels = {"member_since": "No Babinha desde (opcional)"}
        help_texts = {
            "pote": POTE_HELP,
            "member_since": "Se já jogava antes do app, deixa de ser estreante nos times.",
        }

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        place_after(self, "mensalista", "player_type")
        # This is somebody else's contact: the browser must not autofill the board's own.
        self.fields["phone"].widget.attrs["autocomplete"] = "off"
        self.fields["email"].widget.attrs["autocomplete"] = "off"

    clean_member_since = PlayerForm.clean_member_since


class CancelForm(forms.Form):
    reason = forms.CharField(label="Motivo", max_length=200)


class ForgiveForm(forms.Form):
    note = forms.CharField(
        label="Motivo do perdão",
        max_length=200,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
