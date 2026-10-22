from django.urls import path, re_path

from finance import views

app_name = "finance"

urlpatterns = [
    path("pagamentos/", views.payments, name="payments"),
    path(
        "pagamentos/<int:charge_id>/comprovante/",
        views.upload_receipt,
        name="upload_receipt",
    ),
    path("mensalista/", views.join_monthly, name="join_monthly"),
    path("comprovantes/<int:receipt_id>/", views.receipt_file, name="receipt_file"),
    path("caixa/", views.report, name="report"),
    re_path(r"^caixa/(?P<month>\d{4}-\d{2})/$", views.report, name="report_month"),
]
