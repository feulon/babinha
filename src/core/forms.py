import re
from decimal import Decimal

from django import forms
from django.core.validators import MinValueValidator

from core.models import ZERO, Feedback, FeedbackKind

MAX_WHOLE_DIGITS_MESSAGE = "Valor alto demais. O máximo é 999.999,99."

# "1.000", "12.345.678": dots in groups of three are thousands separators, as in "1.000,50".
_THOUSANDS = re.compile(r"\d{1,3}(\.\d{3})+")


def normalize_money_text(raw: str) -> str:
    """What people type for an amount ("R$ 1.000,5", "25.50", "1.000") as the Brazilian text
    Django parses ("1000,5", "25.50", "1000"): thousand dots go away, the comma stays."""
    text = re.sub(r"(?i)r\$|\s", "", raw)
    if "," in text:
        return text.replace(".", "")
    if _THOUSANDS.fullmatch(text):
        return text.replace(".", "")
    return text


class MoneyInput(forms.TextInput):
    """Brazilian format ("25,00") needs a text input: number inputs reject the comma. It also
    takes "1.000,50" and "R$ 25": the dots of thousands are not a decimal point."""

    is_localized = True

    def __init__(self, attrs: dict | None = None) -> None:
        super().__init__({"inputmode": "decimal", "placeholder": "0,00", **(attrs or {})})

    def value_from_datadict(self, data: object, files: object, name: str) -> object:
        value = super().value_from_datadict(data, files, name)
        return normalize_money_text(value) if isinstance(value, str) else value


def localize_money(
    field: forms.DecimalField,
    *,
    minimum: Decimal = ZERO,
    below_minimum: str = "O valor não pode ser negativo.",
) -> None:
    """Make a model's money field a Brazilian one: "25,00" input and plain pt-BR messages.

    The lower bound is checked here, with a readable message, before the model's own check."""
    field.localize = True
    field.widget = MoneyInput()
    field.error_messages.update(
        invalid="Informe um valor em reais, por exemplo 25,00.",
        max_decimal_places="Use no máximo 2 casas depois da vírgula, por exemplo 25,00.",
        max_digits=MAX_WHOLE_DIGITS_MESSAGE,
        max_whole_digits=MAX_WHOLE_DIGITS_MESSAGE,
    )
    field.validators.append(MinValueValidator(minimum, below_minimum))


class FeedbackForm(forms.ModelForm):
    kind = forms.ChoiceField(
        label="O que é?",
        choices=FeedbackKind.choices,
        widget=forms.RadioSelect,
        error_messages={"required": "Escolha se é um bug ou uma sugestão."},
    )

    class Meta:
        model = Feedback
        fields = ["kind", "text"]
        widgets = {"text": forms.Textarea(attrs={"rows": 5, "maxlength": Feedback.MAX_TEXT_LENGTH})}
        labels = {"text": "Conta pra gente"}
        error_messages = {
            "text": {
                "required": "Escreva a mensagem antes de enviar.",
                "max_length": f"Passou de {Feedback.MAX_TEXT_LENGTH} caracteres. Resuma um pouco.",
            },
        }

    def clean_text(self) -> str:
        text = self.cleaned_data["text"].strip()
        if not text:
            raise forms.ValidationError(self.fields["text"].error_messages["required"])
        return text
