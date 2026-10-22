from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def brl(value: Decimal | int | None) -> str:
    """Brazilian reais with the sign in front: 1234.5 -> "R$ 1.234,50", -620 -> "-R$ 620,00"."""
    amount = Decimal(value or 0)
    digits = f"{abs(amount):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{'-' if amount < 0 else ''}R$ {digits}"


@register.filter
def increase_percent(factor: Decimal | None) -> str:
    """A multiplier as the percent it adds: 1.5 -> "50", 1.25 -> "25", 1.333 -> "33,3"."""
    percent = ((Decimal(factor or 1) - 1) * 100).quantize(Decimal("0.01")).normalize()
    return f"{percent:f}".replace(".", ",")
