from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.utils.cache import patch_cache_control

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "connect-src 'self'",
        "manifest-src 'self'",
        "worker-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
    ]
)


class ContentSecurityPolicyMiddleware:
    """All scripts and styles are served from our static files, so inline code is not allowed."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response


class PrivatePagesMiddleware:
    """A page shown to a signed-in player carries their phone, e-mail and payments, so the browser
    must not keep it: after Sair, the back button asks the server again and meets the login."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if request.user.is_authenticated and response.headers.get("Content-Type", "").startswith(
            "text/html"
        ):
            patch_cache_control(response, private=True, no_store=True)
        return response
