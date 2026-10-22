from django.urls import path

from games import views

app_name = "games"

urlpatterns = [
    path("", views.home, name="home"),
    path("lista/entrar/", views.join, name="join"),
    path("lista/sair/", views.leave, name="leave"),
]
