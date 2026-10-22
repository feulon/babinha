"""Connecting a player's referral to a registered player, from the profile."""

from django.db.models import QuerySet

from accounts.models import Player


def descendant_ids(player: Player) -> set[int]:
    """Everyone who came, directly or not, through this player: they cannot be their referrer."""
    found: set[int] = set()
    frontier = [player.pk]
    while frontier:
        children = set(
            Player.objects.filter(referred_by_id__in=frontier).values_list("pk", flat=True)
        )
        frontier = list(children - found)
        found |= children
    return found


def candidates(player: Player, query: str = "") -> QuerySet[Player]:
    """Active players the person can pick as their referrer, filtered by name."""
    blocked = descendant_ids(player) | {player.pk}
    found = Player.objects.filter(is_active=True).exclude(pk__in=blocked)
    query = query.strip()
    if query:
        found = found.filter(name__icontains=query)
    return found.order_by("name", "pk")


def connect(player: Player, referrer: Player) -> None:
    player.referred_by = referrer
    player.save(update_fields=["referred_by"])


def disconnect(player: Player) -> None:
    """Drops the link and the typed text: the person has nobody to credit any more."""
    player.referred_by = None
    player.referrer_text = ""
    player.save(update_fields=["referred_by", "referrer_text"])
