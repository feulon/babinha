from django.urls import path

from community import views

app_name = "community"

urlpatterns = [
    path("bola-de-ouro/", views.gold_ball, name="gold_ball"),
    path("bola-de-ouro/<str:day>/", views.gold_ball, name="gold_ball_day"),
    path("bola-de-ouro/<str:day>/votar/", views.vote, name="vote"),
    path("ranking/", views.ranking, name="ranking"),
    path("jogadores/", views.players, name="players"),
    path("jogador/<int:player_id>/", views.player, name="player"),
    path("diretoria/temporada/", views.season, name="season"),
    path("diretoria/temporada/encerrar/", views.end_season, name="end_season"),
]
