from django.contrib import admin
from django.urls import include, path

from core import views as core_views

handler500 = "core.views.server_error"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("games.urls")),
    path("", include("accounts.urls")),
    path("", include("fines.urls")),
    path("", include("finance.urls")),
    path("", include("matchday.urls")),
    path("", include("community.urls")),
    path("diretoria/", include("board.urls")),
    path("regras/", core_views.rules, name="rules"),
    path("sobre/", core_views.about, name="about"),
    path("reportar/", core_views.feedback, name="feedback"),
    path("mais/", core_views.more, name="more"),
    path("tutorial/", core_views.tutorial, name="tutorial"),
    path("modo/", core_views.former_view_mode, name="former_view_mode"),
    path("manifest.webmanifest", core_views.manifest, name="manifest"),
    path("sw.js", core_views.service_worker, name="service_worker"),
    path("offline/", core_views.offline, name="offline"),
    path("saude/", core_views.health, name="health"),
]
