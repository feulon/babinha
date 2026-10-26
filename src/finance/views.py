import logging
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.formats import date_format
from django.views.decorators.http import require_GET, require_POST

from core import clock, navigation
from finance import charges, monthly, receipt_files
from finance.forms import ReceiptForm
from finance.models import Receipt
from finance.selectors import month_report, monthly_plan, pix_info, player_payments
from finance.services import PaymentError, submit_receipt

logger = logging.getLogger(__name__)


def payments_context(request: HttpRequest) -> dict:
    """What the player owes, the Pix key and the upload form (home and payments screens)."""
    today = clock.now().date()
    charges.ensure_monthly_charges(player=request.user)
    return {
        "payments": player_payments(request.user, today),
        "plan": monthly_plan(request.user),
        "pix": pix_info(),
        "receipt_accept": receipt_files.ACCEPT_ATTR,
        "max_receipt_size": receipt_files.MAX_SIZE_LABEL,
    }


@login_required
@require_GET
def payments(request: HttpRequest) -> HttpResponse:
    return render(request, "finance/payments.html", payments_context(request))


@login_required
@require_POST
def join_monthly(request: HttpRequest) -> HttpResponse:
    """The "Virar mensalista" button: join the plan month, then go pay the fee (or later)."""
    try:
        enrollment, _ = monthly.join(request.user)
    except monthly.MonthlyError as error:
        messages.error(request, error.message)
        return redirect("games:home")
    plan = monthly_plan(request.user, month=enrollment.month)
    month = date_format(plan.month, "F").lower()
    if plan.charge is None:
        messages.success(request, f"Você é mensalista de {month}.")
        return redirect("games:home")
    messages.success(
        request,
        f"Você é mensalista de {month}! Falta pagar a mensalidade: faça o Pix e envie o "
        "comprovante abaixo. Pode ser depois, mas o ideal é agora.",
    )
    return redirect("finance:payments")


def _back(request: HttpRequest) -> HttpResponse:
    return redirect(navigation.requested_next(request) or "finance:payments")


@login_required
@require_POST
def upload_receipt(request: HttpRequest, charge_id: int) -> HttpResponse:
    form = ReceiptForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, form.errors["file"][0])
        return _back(request)
    try:
        submit_receipt(charge_id, request.user, form.cleaned_data["file"])
    except PermissionDenied as exc:
        raise Http404 from exc
    except (PaymentError, receipt_files.ReceiptFileError) as error:
        messages.error(request, error.message)
    else:
        messages.success(request, "Comprovante enviado! A diretoria vai conferir.")
    return _back(request)


@login_required
@require_GET
def receipt_file(request: HttpRequest, receipt_id: int) -> FileResponse:
    """Receipts are private: only the player who owes the charge and the diretoria see them."""
    receipt = get_object_or_404(Receipt.objects.select_related("charge"), pk=receipt_id)
    if receipt.charge.player_id != request.user.pk and not request.user.is_board:
        raise Http404
    try:
        handle = receipt.file.open("rb")
    except Exception as exc:
        logger.exception("Could not read receipt %s from storage", receipt.pk)
        raise Http404 from exc
    extension = receipt_files.KINDS_BY_CONTENT_TYPE[receipt.content_type].extension
    response = FileResponse(
        handle,
        content_type=receipt.content_type,
        filename=f"comprovante-{receipt.pk}.{extension}",
    )
    response["Cache-Control"] = "private, no-store"
    return response


def parse_month(raw: str | None, today: date) -> date:
    if raw is None:
        return charges.month_start(today)
    try:
        return date.fromisoformat(f"{raw}-01")
    except ValueError as exc:
        raise Http404 from exc


def report_context(month: str | None, *, with_people: bool) -> dict[str, object]:
    """The Caixa of a month (`month` is "YYYY-MM", None for the current one) and its neighbours."""
    today = clock.now().date()
    first = parse_month(month, today)
    return {
        "report": month_report(first, today, with_people=with_people),
        "previous_month": charges.previous_month(first).strftime("%Y-%m"),
        "next_month": charges.next_month(first).strftime("%Y-%m"),
        "is_current_month": first == charges.month_start(today),
    }


@login_required
@require_GET
def report(request: HttpRequest, month: str | None = None) -> HttpResponse:
    """Open finance: the month's totals, the same for everyone. Who paid and the expenses are in
    the diretoria's Caixa (`board:cash`)."""
    return render(request, "finance/report.html", report_context(month, with_people=False))
