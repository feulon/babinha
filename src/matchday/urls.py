from django.urls import path

from matchday import views

app_name = "matchday"

urlpatterns = [
    path("jogo/", views.day, name="today"),
    path("jogo/<str:day>/", views.day, name="day"),
]
