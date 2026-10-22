from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import HttpRequest, HttpResponse, HttpResponseServerError, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_http_methods

from accounts import throttle
from core import demo, navigation
from core.forms import FeedbackForm
from core.models import ClubSettings

THEME_COLOR = "#f3f5f1"


@require_GET
@cache_control(max_age=60 * 60)
def manifest(request: HttpRequest) -> JsonResponse:
    data = {
        "name": "Babinha de Domingo",
        "short_name": "Babinha",
        "description": "Lista, times, placar, ranking e pagamentos do Babinha de domingo.",
        "lang": "pt-BR",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f3f5f1",
        "theme_color": THEME_COLOR,
        "icons": [
            {"src": static("icons/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": static("icons/icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {
                "src": static("icons/icon-maskable-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }
    return JsonResponse(data, content_type="application/manifest+json")


@require_GET
@cache_control(no_cache=True)
def service_worker(request: HttpRequest) -> HttpResponse:
    """Served from the site root so the worker controls every page."""
    context = {
        "precache": [
            "/offline/",
            static("css/app.css"),
            static("js/htmx.min.js"),
            static("js/app.js"),
            static("icons/icon-192.png"),
        ]
    }
    response = render(request, "core/sw.js", context, content_type="text/javascript")
    response["Service-Worker-Allowed"] = "/"
    return response


@require_GET
def offline(request: HttpRequest) -> HttpResponse:
    return render(request, "core/offline.html")


@require_GET
def rules(request: HttpRequest) -> HttpResponse:
    """The rules and amounts of the baba, readable before signing in."""
    context = {"club": ClubSettings.load(), "list_opens_at": settings.BABA_LIST_OPENS_AT}
    return render(request, "core/rules.html", context)


@login_required
@require_GET
def more(request: HttpRequest) -> HttpResponse:
    """The "Mais" tab: a hub of everything about the baba that has no tab of its own."""
    return render(request, "core/more.html")


@login_required
@require_GET
def tutorial(request: HttpRequest) -> HttpResponse:
    """The player tutorial: a read-only walk through the app."""
    return render(request, "core/tutorial.html")


@login_required
@require_GET
def about(request: HttpRequest) -> HttpResponse:
    """The "Sobre o app" screen: the vibe-coded note and, when configured, the creator contact."""
    return render(request, "core/about.html", {"creator_email": settings.BABA_CREATOR_EMAIL})


@login_required
@require_http_methods(["GET", "POST"])
def feedback(request: HttpRequest) -> HttpResponse:
    """The "Reportar bug ou sugestão" screen: the bug / suggestion form."""
    key = str(request.user.pk)
    form = FeedbackForm(request.POST or None)
    blocked_until = (
        throttle.FEEDBACK_ACCOUNT.blocked_until(key) if request.method == "POST" else None
    )
    if request.method == "POST" and blocked_until is None and form.is_valid():
        form.instance.author = request.user
        form.save()
        throttle.FEEDBACK_ACCOUNT.hit(key)
        messages.success(request, "Mensagem enviada! Obrigado, o criador vai ler com carinho.")
        return redirect("feedback")
    context = {"form": form, "blocked_until": blocked_until}
    return render(request, "core/feedback.html", context, status=429 if blocked_until else 200)


@require_GET
def health(request: HttpRequest) -> JsonResponse:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


def server_error(request: HttpRequest) -> HttpResponse:
    """The 500 page. It renders without the context processors (they may be what failed), so the
    demo banner is passed by hand."""
    banner = demo.BANNER if demo.is_on() else ""
    return HttpResponseServerError(render_to_string("500.html", {"demo_banner": banner}))


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """A form (or htmx action) sent a stale CSRF token, usually from a page left open for long."""
    return render(request, "errors/csrf.html", status=403)


@login_required
@require_http_methods(["GET", "POST"])
def former_view_mode(request: HttpRequest) -> HttpResponse:
    """The address of the old player / diretoria switch. The app has a single mode now, so it
    only returns to the screen the switch was pressed on, or to the Início."""
    target = navigation.safe_target(request.POST.get("next") or request.GET.get("next"))
    return redirect(target or "games:home")
