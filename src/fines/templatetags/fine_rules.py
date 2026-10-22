from django import template

from core.models import ClubSettings

register = template.Library()


@register.inclusion_tag("fines/_how_fines_work.html")
def how_fines_work(progression: bool = True) -> dict[str, object]:
    """The short explanation of what generates a fine, with the amounts read from the settings."""
    return {"club": ClubSettings.load(), "progression": progression}


@register.inclusion_tag("fines/_progression.html")
def fine_progression() -> dict[str, object]:
    """The no-show fine escalation, shared by rules and both tutorials."""
    return {"club": ClubSettings.load()}


@register.inclusion_tag("core/help/_late_fine.html")
def late_fine_rules() -> dict[str, object]:
    """The same fixed late tiers used when recording a fine."""
    return {"late_fines": sorted(ClubSettings.load().late_fine_amounts.items())}


@register.simple_tag
def club_settings() -> ClubSettings:
    """The club's configured values, for templates that print amounts outside a view context."""
    return ClubSettings.load()
