from django.contrib.auth.views import LogoutView
from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("entrar/", views.login_view, name="login"),
    path("sair/", LogoutView.as_view(), name="logout"),
    path("cadastro/", views.signup_view, name="signup"),
    path("foto/", views.photo_view, name="photo_edit"),
    path("foto/remover/", views.photo_remove, name="photo_remove"),
    path("fotos/<int:player_id>/", views.photo_file, name="photo"),
    path("fotos/<int:player_id>/ver/", views.photo_page, name="photo_page"),
    path("indicacao/", views.referrer_view, name="referrer"),
    path("contato/", views.contact_view, name="contact"),
    path("senha/alterar/", views.password_change_view, name="password_change"),
    path("senha/recuperar/", views.PasswordResetView.as_view(), name="password_reset"),
    path(
        "senha/recuperar/enviado/",
        views.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "senha/redefinir/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "senha/redefinida/",
        views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
]
