from django.urls import path

from fines import views

app_name = "fines"

urlpatterns = [
    path("multas/", views.my_fines, name="mine"),
]
