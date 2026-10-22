from django.conf import settings
from django.contrib import admin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse

from core.models import Feedback


def login_through_the_app(request: HttpRequest, extra_context: dict | None = None) -> HttpResponse:
    if request.user.is_authenticated:
        raise PermissionDenied
    return redirect_to_login(request.GET.get("next", ""), settings.LOGIN_URL)


admin.site.login = login_through_the_app


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "author", "text")
    list_filter = ("kind",)
    readonly_fields = ("author", "kind", "text", "created_at")

    def has_add_permission(self, request: object) -> bool:
        return False
