from urllib.parse import quote, urlencode

from django import template
from django.urls import reverse

from core import navigation

register = template.Library()


@register.simple_tag(takes_context=True)
def nexturl(context: template.Context, name: str, *args: object, **kwargs: object) -> str:
    """`{% nexturl 'community:player' player_id=3 q_aba='origem' %}`: the address with the current
    screen as its `next`, so the destination keeps the tab of this screen. Arguments named `q_x`
    go to the query string as `x`; the others build the address."""
    query = {key[2:]: kwargs.pop(key) for key in list(kwargs) if key.startswith("q_")}
    url = reverse(name, args=args, kwargs=kwargs)
    if query:
        url = f"{url}?{urlencode(query)}"
    return navigation.with_next(context["request"], url)


@register.simple_tag(takes_context=True)
def back_url(context: template.Context, parent: str) -> str:
    """Where "Voltar" goes: the `next` of the request, else the screen's fixed `parent`."""
    return navigation.requested_next(context["request"]) or parent


@register.inclusion_tag("includes/next_field.html", takes_context=True)
def next_field(context: template.Context) -> dict[str, object]:
    """A hidden `next` so a form keeps the tab of the screen it is on."""
    return {"next": navigation.requested_next(context["request"])}


@register.simple_tag(takes_context=True)
def keep_next(context: template.Context, separator: str = "&") -> str:
    """`&next=...` to append to a link that stays on the same screen (a tab, a filter; pass "?"
    when the address has no query yet), or nothing when the screen has no `next`."""
    target = navigation.requested_next(context["request"])
    return f"{separator}next={quote(target, safe='')}" if target else ""
