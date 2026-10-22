from django import template

register = template.Library()

END_OF_SENTENCE = ".!?…"


@register.filter
def stop(text: str | None) -> str:
    """`reason|stop` in a template: text a person typed, closed by one full stop. It already
    ends a sentence when they typed the stop themselves, so the screen never shows two."""
    text = (text or "").strip()
    return text if not text or text[-1] in END_OF_SENTENCE else f"{text}."
