from django.conf import settings
from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject

from core import clock, demo, fake_clock, navigation


def fake_clock_banner(request: HttpRequest) -> dict[str, object]:
    """`fake_now` is set only while the testing clock override is active."""
    return {"fake_now": clock.now() if fake_clock.is_active() else None}


def demo_banner(request: HttpRequest) -> dict[str, object]:
    """`demo_banner` is the text of the strip shown on every screen while demo mode is on."""
    return {"demo_banner": demo.BANNER if demo.is_on() else ""}


def csrf_cookie(request: HttpRequest) -> dict[str, object]:
    """`csrf_cookie_name` is the cookie the scripts read the current CSRF token from. Demo mode
    renames it, so the page has to say which one it is."""
    return {"csrf_cookie_name": settings.CSRF_COOKIE_NAME}


def monthly_member(request: HttpRequest) -> dict[str, object]:
    """`viewer_is_monthly` is true for a signed-in mensalista of the plan month, for the "M"
    mark on their avatar. Lazy and cached: only screens that show it ask."""
    user = request.user
    if not user.is_authenticated:
        return {"viewer_is_monthly": False}
    return {"viewer_is_monthly": SimpleLazyObject(lambda: user.is_mensalista)}


def navigation_tab(request: HttpRequest) -> dict[str, object]:
    """The tab of the bar that stays lit: the tab of the screen where the `?next=` chain started,
    else the screen's own."""
    match = request.resolver_match
    own = match.view_name if match else ""
    target = navigation.requested_next(request)
    if target is None:
        return {"nav_tab": navigation.tab_of(own), "here_url": request.path}
    origin = navigation.origin_match(target)
    origin_name = origin.view_name if origin else own
    return {"nav_tab": navigation.tab_of(origin_name), "here_url": navigation.here(request)}
