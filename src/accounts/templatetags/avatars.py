from django import template
from django.urls import reverse
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def avatar(name: str, player_id: int, photo: str, css_class: str = "avatar") -> str:
    """The round picture of a player: their photo when they have one, else their initial.

    The photo URL carries a version that changes with the file, so browsers cache it for good.
    """
    if photo:
        version = photo.rsplit("/", 1)[-1].removesuffix(".jpg")
        return format_html(
            '<img class="{} avatar-img" src="{}?v={}" alt="" loading="lazy" decoding="async">',
            css_class,
            reverse("accounts:photo", args=[player_id]),
            version,
        )
    return format_html(
        '<span class="{}" aria-hidden="true">{}</span>', css_class, (name or "?")[:1].upper()
    )
