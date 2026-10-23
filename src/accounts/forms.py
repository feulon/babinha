import logging
import secrets
from collections.abc import Iterator

from django import forms
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.forms.utils import ErrorDict

from accounts import photos, throttle
from accounts.models import Player
from accounts.phone import format_phone, normalize_phone
from accounts.validators import PASSWORD_LENGTH, reject_blocked_password
from core import clock

logger = logging.getLogger(__name__)

PASSWORD_ATTRS = {
    "minlength": PASSWORD_LENGTH,
    "maxlength": PASSWORD_LENGTH,
    "autocomplete": "current-password",
}
WRONG_INVITE_CODE = "Código de convite incorreto. Confira com quem convidou você."


class LoginForm(forms.Form):
    login = forms.CharField(
        label="Celular ou e-mail",
        max_length=254,
        widget=forms.TextInput(
            attrs={"autocomplete": "username", "autofocus": True, "autocapitalize": "none"}
        ),
    )
    password = forms.CharField(
        label="Senha", strip=False, widget=forms.PasswordInput(attrs=PASSWORD_ATTRS)
    )

    def clean_login(self) -> str:
        return self.cleaned_data["login"].strip()


class PhoneField(forms.CharField):
    """A Brazilian mobile typed any way; the cleaned value is the 11 canonical digits."""

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("label", "Celular (com DDD)")
        kwargs.setdefault("max_length", 24)
        kwargs.setdefault(
            "widget",
            forms.TextInput(
                attrs={
                    "type": "tel",
                    "inputmode": "tel",
                    "autocomplete": "tel-national",
                    "placeholder": "(11) 98765-4321",
                    "data-phone-mask": "",
                }
            ),
        )
        super().__init__(**kwargs)

    def clean(self, value: str) -> str:
        value = super().clean(value)
        return normalize_phone(value) if value else ""


class ContactEmailField(forms.EmailField):
    """Required by default; forms for players from before it was required pass required=False."""

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("label", "E-mail")
        kwargs.setdefault("widget", forms.EmailInput(attrs={"autocomplete": "email"}))
        super().__init__(**kwargs)

    def clean(self, value: str) -> str:
        return super().clean(value).strip().lower()


class ContactCleanMixin:
    """Phone and e-mail rules shared by every form that writes them: both are required
    (unless the form relaxes it), and each is unique when filled."""

    instance: Player

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        # Shown the way people write it, not as the stored digits.
        if self.instance.phone:
            self.initial["phone"] = format_phone(self.instance.phone)

    def clean_phone(self) -> str:
        phone = self.cleaned_data["phone"]
        if phone and Player.objects.filter(phone=phone).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Já existe uma conta com este celular.")
        return phone

    def clean_email(self) -> str:
        email = self.cleaned_data["email"]
        if (
            email
            and Player.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists()
        ):
            raise forms.ValidationError("Já existe uma conta com este e-mail.")
        return email


class PhotoField(forms.FileField):
    """An optional picture, checked and shrunk while the form validates (see accounts.photos)."""

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("label", "Foto")
        kwargs.setdefault("required", False)
        kwargs.setdefault(
            "widget", forms.FileInput(attrs={"accept": photos.ACCEPT_ATTR, "data-photo-input": ""})
        )
        super().__init__(**kwargs)

    def clean(self, data: object, initial: object = None) -> bytes | None:
        upload = super().clean(data, initial)
        if not upload:
            return None
        try:
            return photos.process(upload)
        except photos.PhotoError as error:
            raise forms.ValidationError(error.message) from error


class PhotoFormMixin:
    """Adds the optional photo field. The cleaned value is the processed JPEG (or None); call
    apply_photo once the player is saved."""

    def apply_photo(self, player: Player) -> bool:
        """Store the chosen photo (or drop it when asked to). False when the storage failed:
        the rest of the form is already saved, so that must not undo it."""
        data = self.cleaned_data.get("photo")
        remove = self.cleaned_data.get("remove_photo", False)
        if data is None and not remove:
            return True
        try:
            photos.replace(player, data)
        except Exception:
            logger.exception("Could not store the profile photo of player %s", player.pk)
            return False
        return True


class ReferrerField(forms.ModelChoiceField):
    def label_from_instance(self, obj: Player) -> str:
        return obj.board_label


class NameCleanMixin:
    """Name and nickname are both required and kept as one line of single-spaced words."""

    cleaned_data: dict

    def clean_name(self) -> str:
        name = " ".join(self.cleaned_data["name"].split())
        if not name:
            raise forms.ValidationError("Informe seu nome.")
        return name

    def clean_nickname(self) -> str:
        nickname = " ".join(self.cleaned_data["nickname"].split())
        if not nickname:
            raise forms.ValidationError("Informe seu apelido.")
        return nickname


class SignupForm(NameCleanMixin, PhotoFormMixin, ContactCleanMixin, forms.ModelForm):
    photo = PhotoField(
        label="Foto (opcional, mas recomendada)",
        help_text="Você pode trocar depois.",
    )
    phone = PhoneField(help_text="Número WhatsApp.")
    email = ContactEmailField(help_text="Receber Avisos e Novidades")
    password = forms.CharField(
        label="Senha (4 caracteres)",
        strip=False,
        widget=forms.PasswordInput(attrs={**PASSWORD_ATTRS, "autocomplete": "new-password"}),
    )
    password_confirm = forms.CharField(
        label="Repita a senha",
        strip=False,
        widget=forms.PasswordInput(attrs={**PASSWORD_ATTRS, "autocomplete": "new-password"}),
    )
    # Free text, never a list: the sign-up page is public and must not show who is in the group.
    # The player connects it to a registered player later, from their profile.
    referrer_name = forms.CharField(
        label="Quem indicou você (opcional)",
        required=False,
        max_length=60,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
        help_text="Só o nome. Depois você liga a um jogador no perfil.",
    )

    accept_rules = forms.BooleanField(
        label="Li e estou ciente das regras",
        error_messages={"required": "Marque que leu e está ciente das regras para criar a conta."},
    )

    field_order = [
        "invite_code",
        "name",
        "nickname",
        "photo",
        "phone",
        "email",
        "referrer_name",
        "password",
        "password_confirm",
        "accept_rules",
    ]

    class Meta:
        model = Player
        fields = ["name", "nickname", "phone", "email"]
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "name"}),
            "nickname": forms.TextInput(attrs={"autocomplete": "nickname"}),
        }

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.invite_code = settings.BABA_INVITE_CODE
        if self.invite_code:
            self.fields["invite_code"] = forms.CharField(
                label="Código de convite",
                max_length=64,
                widget=forms.TextInput(attrs={"autocomplete": "off", "autocapitalize": "none"}),
                help_text="Peça a quem convidou você.",
            )
            self.order_fields(self.field_order)

    def _invite_code_is_right(self) -> bool:
        if not self.invite_code:
            return True
        typed = str(self["invite_code"].value() or "").strip()
        return secrets.compare_digest(typed.lower().encode(), self.invite_code.lower().encode())

    def full_clean(self) -> None:
        if not self.is_bound or self._invite_code_is_right():
            super().full_clean()
            return
        # Nothing else is checked, not even the photo: with a wrong code the answer must not tell
        # a stranger which phones and e-mails already have an account.
        self._errors = ErrorDict(renderer=self.renderer)
        self.cleaned_data = {}
        self.add_error("invite_code", WRONG_INVITE_CODE)

    def clean_invite_code(self) -> str:
        return self.cleaned_data["invite_code"].strip()

    def clean_referrer_name(self) -> str:
        return " ".join(self.cleaned_data["referrer_name"].split())

    def clean_password(self) -> str:
        password = self.cleaned_data["password"]
        reject_blocked_password(password)
        return password

    def clean(self) -> dict:
        cleaned = super().clean()
        password = cleaned.get("password")
        if password and password != cleaned.get("password_confirm"):
            self.add_error("password_confirm", "As senhas não conferem.")
        elif password:
            try:
                password_validation.validate_password(password)
            except forms.ValidationError as error:
                self.add_error("password", error)
        return cleaned

    def save(self, commit: bool = True) -> Player:
        player = Player.objects.create_player(
            phone=self.cleaned_data["phone"],
            email=self.cleaned_data["email"],
            password=self.cleaned_data["password"],
            name=self.cleaned_data["name"],
            nickname=self.cleaned_data["nickname"],
            referred_by=None,
            referrer_text=self.cleaned_data["referrer_name"],
            rules_accepted_at=clock.now(),
        )
        # The account exists either way: a photo that fails to store can be sent again later.
        self.photo_saved = self.apply_photo(player)
        return player


class ContactForm(ContactCleanMixin, forms.ModelForm):
    """How a player edits their own phone and e-mail."""

    phone = PhoneField()
    email = ContactEmailField(
        required=False,
        help_text="Serve para receber o link de nova senha e os avisos de pagamento.",
    )

    class Meta:
        model = Player
        fields = ["phone", "email"]


class ProfileForm(NameCleanMixin, forms.ModelForm):
    """How a player edits their own name and nickname."""

    class Meta:
        model = Player
        fields = ["name", "nickname"]
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "name"}),
            "nickname": forms.TextInput(attrs={"autocomplete": "nickname"}),
        }


class PhotoForm(PhotoFormMixin, forms.Form):
    """How a player sends or swaps their own photo."""

    photo = PhotoField(
        label="Escolher foto",
        required=True,
        help_text=f"JPG, PNG ou WebP, até {photos.MAX_SIZE_LABEL}.",
    )


class ThrottledPasswordResetForm(PasswordResetForm):
    """Sends at most a few reset e-mails per account per hour, without revealing whether the
    account exists (the page always says the e-mail was sent)."""

    email = forms.EmailField(
        label="E-mail",
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autocapitalize": "none"}),
        error_messages={
            "required": "Informe o seu e-mail.",
            "invalid": "Informe um e-mail válido.",
        },
    )

    def get_users(self, email: str) -> Iterator[Player]:
        normalized = email.strip().lower()
        if not normalized:
            return iter(())
        if throttle.PASSWORD_RESET_ACCOUNT.blocked_until(normalized) is not None:
            return iter(())
        throttle.PASSWORD_RESET_ACCOUNT.hit(normalized)
        return (
            player
            for player in Player.objects.filter(email__iexact=normalized, is_active=True)
            if player.has_usable_password()
        )


NEW_PASSWORD_ATTRS = {**PASSWORD_ATTRS, "autocomplete": "new-password"}


class ProvisionalPasswordMixin:
    """A password the player chose is no longer the provisional one the diretoria handed out,
    and it can not be the blocked joke one."""

    def clean_new_password1(self) -> str:
        password = self.cleaned_data["new_password1"]
        reject_blocked_password(password)
        return password

    def save(self, commit: bool = True) -> Player:
        user = super().save(commit=False)
        user.password_is_provisional = False
        if commit:
            user.save(update_fields=["password", "password_is_provisional"])
        return user


class PasswordChangeForm4(ProvisionalPasswordMixin, PasswordChangeForm):
    """The signed-in player trades their password: the current one, the new one twice."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["old_password"].label = "Senha atual"
        self.fields["old_password"].widget.attrs.update(PASSWORD_ATTRS)
        self.fields["new_password1"].label = "Nova senha (4 caracteres)"
        self.fields["new_password2"].label = "Repita a nova senha"
        for name in ("new_password1", "new_password2"):
            self.fields[name].widget.attrs.update(NEW_PASSWORD_ATTRS)
        self.fields["new_password2"].help_text = ""


class PasswordResetSetForm(ProvisionalPasswordMixin, SetPasswordForm):
    """Choosing the new password from the e-mailed link, worded like the sign-up form."""

    error_messages = {
        **SetPasswordForm.error_messages,
        "password_mismatch": "As senhas não conferem.",
    }

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].label = "Nova senha (4 caracteres)"
        self.fields["new_password1"].widget.attrs.update(
            PASSWORD_ATTRS, autocomplete="new-password"
        )
        self.fields["new_password2"].label = "Repita a nova senha"
        self.fields["new_password2"].widget.attrs.update(
            PASSWORD_ATTRS, autocomplete="new-password"
        )
