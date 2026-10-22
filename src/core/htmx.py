"""Helpers for views that answer both full-page requests and HTMX partial requests."""

from django.http import HttpRequest


def is_htmx(request: HttpRequest) -> bool:
    """True when the request came from htmx (a partial swap, not a page load).

    Restoring a page from history (back button) is a page load, so it gets the full page."""
    return (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )
