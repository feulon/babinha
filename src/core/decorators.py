from collections.abc import Callable
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse

View = Callable[..., HttpResponse]


def board_required(view: View) -> View:
    """Only diretoria and presidente may use the view."""

    @login_required
    @wraps(view)
    def wrapper(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        if not request.user.is_board:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def presidente_required(view: View) -> View:
    """Only the presidente (or the hidden admin) may use the view: what the diretoria can see
    but not change."""

    @login_required
    @wraps(view)
    def wrapper(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        if not request.user.has_presidente_powers:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper
