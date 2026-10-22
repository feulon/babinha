from django import template

from accounts.models import Player
from community.selectors import home_card

register = template.Library()


@register.inclusion_tag("community/_home_card.html", takes_context=True)
def gold_ball_card(context: template.Context, viewer: Player, slot: str = "below") -> dict:
    """The home screen's nudge to vote, or the latest Bola de Ouro.

    While the vote is open the card leads the page (`slot="top"`); a finished vote keeps its
    place further down (`slot="below"`). Each call renders only its own slot."""
    card = home_card(viewer)
    if card is None or card.view.window.is_open != (slot == "top"):
        return {"request": context["request"], "card": None}
    return {"request": context["request"], "card": card}
