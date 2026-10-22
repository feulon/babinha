from datetime import date, timedelta
from decimal import Decimal

from django import forms

from core import clock
from core.forms import localize_money
from finance import receipt_files
from finance.models import Expense
from finance.services import REASON_MAX

CENT = Decimal("0.01")
# An expense dated further back than this is probably a typo in the year.
UNUSUAL_PAST = timedelta(days=365)


class ReceiptForm(forms.Form):
    file = forms.FileField(
        label="Comprovante do Pix (foto ou PDF)",
        widget=forms.FileInput(attrs={"accept": receipt_files.ACCEPT_ATTR}),
        error_messages={"required": "Escolha a foto ou o PDF do comprovante."},
    )


class RefuseForm(forms.Form):
    reason = forms.CharField(
        label="Motivo da recusa",
        max_length=REASON_MAX,
        widget=forms.Textarea(attrs={"rows": 2}),
    )


class WaiveForm(forms.Form):
    reason = forms.CharField(
        label="Motivo da dispensa",
        max_length=REASON_MAX,
        widget=forms.Textarea(attrs={"rows": 2}),
    )


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["amount", "description", "date"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args: object, today: date | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.today = today or clock.now().date()
        localize_money(
            self.fields["amount"], minimum=CENT, below_minimum="Informe um valor maior que zero."
        )
        self.fields["date"].input_formats = ["%Y-%m-%d", "%d/%m/%Y"]
        if not self.instance.pk:
            self.initial.setdefault("amount", None)
            self.initial.setdefault("date", self.today)
        if self.is_bound and "confirm_date" in self.data:
            self._ask_to_confirm_date()

    def _ask_to_confirm_date(self) -> None:
        self.fields["confirm_date"] = forms.BooleanField(
            required=False, label="A data está certa, lançar mesmo assim"
        )

    def clean(self) -> dict:
        cleaned = super().clean()
        day = cleaned.get("date")
        if day is None or day == self.initial.get("date") or not self._is_unusual(day):
            return cleaned
        if not cleaned.get("confirm_date"):
            if "confirm_date" not in self.fields:
                self._ask_to_confirm_date()
            self.add_error(
                "date",
                f"A data {day:%d/%m/%Y} {self._why_unusual(day)}. "
                "Se estiver certa, marque a caixa abaixo e envie de novo.",
            )
        return cleaned

    def _is_unusual(self, day: date) -> bool:
        return day > self.today or day < self.today - UNUSUAL_PAST

    def _why_unusual(self, day: date) -> str:
        return "é no futuro" if day > self.today else "é de mais de um ano atrás"
