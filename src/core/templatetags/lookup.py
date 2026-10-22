from collections.abc import Mapping

from django import template

register = template.Library()


@register.filter
def get(mapping: Mapping | None, key: object) -> object:
    """`mapping|get:key` in a template: the value for `key`, or nothing when it is missing."""
    return None if mapping is None else mapping.get(key)
