from collections.abc import Callable
from urllib.parse import parse_qs, urlencode, urlsplit

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, resolve_url
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from core.htmx import is_htmx

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Everything a signed-in player without a phone may still open: the page that asks for it, the
# way out, and the endpoints the browser and the service worker call on their own.
EXEMPT_VIEWS = frozenset(
    {"accounts:contact", "accounts:logout", "health", "service_worker", "manifest", "offline"}
)


class PhoneRequiredMiddleware:
    """The phone is mandatory, but players who signed up before it existed have none. Their next
    request, whatever it is, lands on the contact page until they give one."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_view(
        self,
        request: HttpRequest,
        view_func: Callable[..., HttpResponse],
        view_args: tuple,
        view_kwargs: dict,
    ) -> HttpResponse | None:
        user = request.user
        if not user.is_authenticated or user.phone:
            return None
        if request.resolver_match and request.resolver_match.view_name in EXEMPT_VIEWS:
            return None
        target = reverse("accounts:contact")
        if request.method == "GET":
            target = f"{target}?{urlencode({'next': request.get_full_path()})}"
        if is_htmx(request):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = target
            return response
        return redirect(target)


class LoginRedirectMiddleware:
    """An action sent after the session ended is answered with a redirect to the login page, and
    the action's own URL ends up in `next`. Signing in would then open that URL with a GET, which
    most actions refuse (HTTP 405), and an htmx action would even swap the login page into a small
    block of the screen. Send the browser to the login page instead, coming back to the screen the
    action was sent from: the htmx current URL, or the Referer of a plain form post."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if not response.has_header("Location"):
            return response
        login_path = resolve_url(settings.LOGIN_URL)
        location = urlsplit(response["Location"])
        # Only the redirect of a protected view (it asks to come back): Sair also ends at the login.
        if location.path != login_path or "next" not in parse_qs(location.query):
            return response
        if is_htmx(request):
            response.status_code = 204
            response["HX-Redirect"] = self._login_url(
                request, login_path, request.headers.get("HX-Current-URL", ""), response["Location"]
            )
            del response["Location"]
        elif request.method not in SAFE_METHODS:
            # No known screen: the login page alone, the action's URL is no place to return to.
            response["Location"] = self._login_url(
                request, login_path, request.headers.get("Referer", ""), login_path
            )
        return response

    @staticmethod
    def _login_url(request: HttpRequest, login_path: str, screen: str, fallback: str) -> str:
        if not url_has_allowed_host_and_scheme(
            screen, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return fallback
        parts = urlsplit(screen)
        if parts.path == login_path:
            return fallback
        back = f"{parts.path}?{parts.query}" if parts.query else parts.path
        return f"{login_path}?{urlencode({'next': back})}"
