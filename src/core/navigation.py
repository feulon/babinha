"""Where a screen came from: the `?next=` carried from one screen to the next, and the tab of
the bar each screen belongs to.

Opening Jogos from the Início carries `?next=/`; from there the Bola de Ouro carries
`?next=/jogo/%3Fnext%3D/`, and so on. The tab bar keeps the tab where the chain started.
"""

from urllib.parse import parse_qs, urlencode, urlsplit

from django.http import HttpRequest
from django.urls import Resolver404, ResolverMatch, resolve
from django.utils.http import url_has_allowed_host_and_scheme

PARAM = "next"
MAX_DEPTH = 4
# Links from one origin screen to another stack their `next`s; past this the chain is dropped and
# the destination goes back to its fixed parent.
MAX_NEXT_LENGTH = 400


def safe_target(target: str | None) -> str | None:
    """A path on this site, never another host."""
    if target and target.startswith("/") and url_has_allowed_host_and_scheme(target, set()):
        return target
    return None


def next_of(request: HttpRequest) -> str | None:
    return safe_target(request.GET.get(PARAM))


def requested_next(request: HttpRequest) -> str | None:
    """The `next` of the request (query or form) when it is an address of this site, else None.
    A `next` that points at the screen being shown would only loop, so it counts as none."""
    target = safe_target(request.POST.get(PARAM) or request.GET.get(PARAM))
    if target is None or target.split("?", 1)[0] == request.path:
        return None
    return target


def here_with_query(request: HttpRequest) -> str:
    """This screen's address with its own query (a tab, a filter) and a sanitised `next`: the
    one received is never echoed back unless it is an address of this site."""
    query = [(key, value) for key, value in request.GET.lists() if key != PARAM]
    pairs = [(key, item) for key, values in query for item in values]
    target = next_of(request)
    if target:
        pairs.append((PARAM, target))
    return f"{request.path}?{urlencode(pairs)}" if pairs else request.path


def with_next(request: HttpRequest, url: str) -> str:
    """`url` with the current screen as its `next`."""
    current = here_with_query(request)
    if len(current) > MAX_NEXT_LENGTH:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode({PARAM: current})}"


def here(request: HttpRequest) -> str:
    """This screen's own address, keeping the way back (a stray `next` is dropped)."""
    target = next_of(request)
    return f"{request.path}?{urlencode({PARAM: target})}" if target else request.path


def view_name_of(target: str) -> str | None:
    try:
        return resolve(urlsplit(target).path).view_name
    except Resolver404:
        return None


def origin_match(target: str) -> ResolverMatch | None:
    """The screen where the chain of `next` links started."""
    for _ in range(MAX_DEPTH):
        deeper = safe_target((parse_qs(urlsplit(target).query).get(PARAM) or [None])[0])
        if deeper is None:
            break
        target = deeper
    try:
        return resolve(urlsplit(target).path)
    except Resolver404:
        return None


# The tabs of the bar. Diretoria is shown only to the diretoria; the server still checks every
# diretoria screen with `board_required`.
INICIO, DOMINGO, TURMA, MAIS, DIRETORIA = "inicio", "domingo", "turma", "mais", "diretoria"
_TABS = {
    "games:home": INICIO,
    "matchday:today": DOMINGO,
    "matchday:day": DOMINGO,
    "community:players": TURMA,
    "community:player": TURMA,
    "community:ranking": TURMA,
    "community:gold_ball": TURMA,
    "community:gold_ball_day": TURMA,
    "community:vote": TURMA,
    "accounts:photo_page": TURMA,
    "more": MAIS,
    "finance:payments": MAIS,
    "finance:report": MAIS,
    "finance:report_month": MAIS,
    "fines:mine": MAIS,
    "rules": MAIS,
    "tutorial": MAIS,
    "about": MAIS,
    "feedback": MAIS,
    "accounts:photo_edit": MAIS,
    "accounts:contact": MAIS,
    "accounts:password_change": MAIS,
    "accounts:referrer": MAIS,
    "community:season": DIRETORIA,
    "community:end_season": DIRETORIA,
}


def tab_of(view_name: str | None) -> str:
    """The tab of the bar a screen belongs to, or "" for a screen outside the bar."""
    name = view_name or ""
    if name.startswith("board:"):
        return DIRETORIA
    return _TABS.get(name, "")
