"""Diretoria screens for money: receipt review, pending charges and expenses."""

from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.formats import date_format
from django.views.decorators.http import require_http_methods, require_POST

from core import clock, navigation
from core.decorators import board_required
from core.models import ZERO
from core.templatetags.money import brl
from finance import charges, services
from finance.forms import ExpenseForm, RefuseForm, WaiveForm
from finance.models import Charge, ChargeStatus, Expense, PaymentMethod, Receipt
from finance.selectors import late_mensalistas, month_members, open_charges, review_queue
from finance.views import parse_month, report_context

# The A receber view that leaves out the charges whose receipt already waits for a conference.
UNPAID_FILTER = "sem-comprovante"


@board_required
def receipts(request: HttpRequest) -> HttpResponse:
    return render(request, "board/receipts.html", {"receipts": review_queue()})


def _receipt_url(request: HttpRequest, receipt: Receipt) -> str:
    """The review page again, still going back to where the diretoria came from."""
    url = reverse("board:receipt", kwargs={"receipt_id": receipt.pk})
    origin = navigation.requested_next(request)
    return f"{url}?{urlencode({navigation.PARAM: origin})}" if origin else url


def _review_page(request: HttpRequest, receipt: Receipt, refuse_form: RefuseForm) -> HttpResponse:
    context = {
        "receipt": receipt,
        "charge": receipt.charge,
        "earlier": receipt.charge.receipts.exclude(pk=receipt.pk).select_related("reviewed_by"),
        "refuse_form": refuse_form,
        "can_undo": services.can_undo_approval(receipt),
    }
    return render(request, "board/receipt_review.html", context)


def _receipt_or_404(receipt_id: int) -> Receipt:
    related = ("charge", "charge__player", "charge__game_day", "reviewed_by", "submitted_by")
    return get_object_or_404(Receipt.objects.select_related(*related), pk=receipt_id)


@board_required
def receipt_review(request: HttpRequest, receipt_id: int) -> HttpResponse:
    return _review_page(request, _receipt_or_404(receipt_id), RefuseForm())


@board_required
@require_POST
def approve_receipt(request: HttpRequest, receipt_id: int) -> HttpResponse:
    receipt = _receipt_or_404(receipt_id)
    try:
        services.approve_receipt(receipt.pk, request.user)
    except services.PaymentError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, f"Pagamento de {receipt.charge.player.name} confirmado.")
        return redirect(_receipt_url(request, receipt))  # where "Desfazer" lives
    return redirect(navigation.requested_next(request) or "board:receipts")


@board_required
@require_POST
def undo_approval(request: HttpRequest, receipt_id: int) -> HttpResponse:
    receipt = _receipt_or_404(receipt_id)
    try:
        services.undo_approval(receipt.pk, request.user)
    except services.PaymentError as error:
        messages.error(request, error.message)
    else:
        messages.success(
            request, f"Confirmação de {receipt.charge.player.name} desfeita. Confira de novo."
        )
    return redirect(_receipt_url(request, receipt))


@board_required
@require_POST
def refuse_receipt(request: HttpRequest, receipt_id: int) -> HttpResponse:
    receipt = _receipt_or_404(receipt_id)
    form = RefuseForm(request.POST)
    if not form.is_valid():
        return _review_page(request, receipt, form)
    try:
        services.refuse_receipt(receipt.pk, request.user, form.cleaned_data["reason"])
    except services.PaymentError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, f"Comprovante de {receipt.charge.player.name} recusado.")
    return redirect(navigation.requested_next(request) or "board:receipts")


@board_required
def pending(request: HttpRequest) -> HttpResponse:
    today = clock.now().date()
    charges.ensure_monthly_charges(today)
    charges.settle_finished_games()
    only_unpaid = request.GET.get("filtro") == UNPAID_FILTER
    rows = open_charges(today)
    if only_unpaid:
        rows = tuple(row for row in rows if row.charge.status != ChargeStatus.UNDER_REVIEW)
    members = month_members(today, today)
    context = {
        "month": charges.month_start(today),
        "members": len(members),
        "members_paid": sum(1 for member in members if member.is_paid),
        "members_open": sum(1 for member in members if member.is_open),
        "late": late_mensalistas(today),
        "rows": rows,
        "receivable": UNPAID_FILTER if only_unpaid else "todas",
        "total": sum((row.charge.amount for row in rows), ZERO),
        "receipts_to_review": review_queue().count(),
    }
    return render(request, "board/pending.html", context)


@board_required
def cash(request: HttpRequest, month: str | None = None) -> HttpResponse:
    """The diretoria's Caixa: the month's totals with who paid, who owes and the expenses."""
    today = clock.now().date()
    charges.ensure_monthly_charges(today)
    charges.settle_finished_games()
    return render(request, "board/cash.html", report_context(month, with_people=True))


@board_required
@require_http_methods(["GET", "POST"])
def waive_charge(request: HttpRequest, charge_id: int) -> HttpResponse:
    charge = get_object_or_404(Charge.objects.select_related("player"), pk=charge_id)
    form = WaiveForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.waive_charge(charge.pk, request.user, form.cleaned_data["reason"])
        except services.PaymentError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, f"Cobrança de {charge.player.name} dispensada.")
        return redirect("board:pending")
    return render(request, "board/waive.html", {"charge": charge, "form": form})


@board_required
@require_POST
def mark_paid(request: HttpRequest, charge_id: int) -> HttpResponse:
    """One tap: cash, no note. Other methods are not offered here."""
    charge = get_object_or_404(Charge.objects.select_related("player"), pk=charge_id)
    try:
        services.mark_paid(charge.pk, request.user, PaymentMethod.CASH)
    except services.PaymentError as error:
        messages.error(request, error.message)
    else:
        messages.success(
            request, f"Pagamento de {charge.player.name} ({brl(charge.amount)}) registrado."
        )
    return redirect(navigation.requested_next(request) or "board:pending")


def _save_expense(request: HttpRequest, form: ExpenseForm, expense: Expense | None) -> Expense:
    data = form.cleaned_data
    return services.save_expense(
        request.user,
        amount=data["amount"],
        description=data["description"],
        day=data["date"],
        expense=expense,
    )


def _expenses_redirect(expense: Expense) -> HttpResponse:
    return redirect("board:expenses_month", month=expense.date.strftime("%Y-%m"))


def _elsewhere(expense: Expense, today: date) -> str:
    """Saving takes the diretoria to the expense's month: say so when it is not this one."""
    if charges.month_start(expense.date) == charges.month_start(today):
        return ""
    return f" em {date_format(expense.date, 'F/Y')}"


@board_required
@require_http_methods(["GET", "POST"])
def expenses(request: HttpRequest, month: str | None = None) -> HttpResponse:
    today = clock.now().date()
    first = parse_month(month, today)
    form = ExpenseForm(request.POST or None, today=today)
    if request.method == "POST" and form.is_valid():
        expense = _save_expense(request, form, None)
        messages.success(request, f"Despesa lançada{_elsewhere(expense, today)}.")
        return _expenses_redirect(expense)
    following = charges.next_month(first)
    context = {
        "month": first,
        "expenses": Expense.objects.filter(date__gte=first, date__lt=following),
        "form": form,
        "previous_month": charges.previous_month(first).strftime("%Y-%m"),
        "next_month": following.strftime("%Y-%m"),
    }
    return render(request, "board/expenses.html", context)


@board_required
@require_http_methods(["GET", "POST"])
def expense_edit(request: HttpRequest, expense_id: int) -> HttpResponse:
    expense = get_object_or_404(Expense.objects.select_related("created_by"), pk=expense_id)
    form = ExpenseForm(request.POST or None, instance=expense)
    if request.method == "POST" and form.is_valid():
        expense = _save_expense(request, form, expense)
        messages.success(request, f"Despesa atualizada{_elsewhere(expense, clock.now().date())}.")
        return _expenses_redirect(expense)
    return render(request, "board/expense_edit.html", {"expense": expense, "form": form})


@board_required
@require_POST
def expense_delete(request: HttpRequest, expense_id: int) -> HttpResponse:
    expense = get_object_or_404(Expense, pk=expense_id)
    services.delete_expense(expense.pk, request.user)
    messages.success(request, "Despesa excluída.")
    return _expenses_redirect(expense)
