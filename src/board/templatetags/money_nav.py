from django import template
from django.urls import reverse

from finance.selectors import review_queue

register = template.Library()

# (key, label, url name), in the order they are shown: where the diretoria's money lives.
SECTIONS = (
    ("receber", "A receber", "board:pending"),
    ("caixa", "Caixa", "board:cash"),
    ("configuracoes", "Configurações", "board:settings"),
)

# The views inside "A receber": (key, label, url name, query string).
RECEIVABLE = (
    ("todas", "Todas", "board:pending", ""),
    ("conferir", "A conferir", "board:receipts", ""),
    ("multas", "Multas", "board:fines", ""),
)


@register.inclusion_tag("board/_money_nav.html")
def money_nav(current: str, receivable: str = "") -> dict[str, object]:
    """The money destinations of the diretoria and, inside A receber, its views.

    `receivable` names the view of A receber being shown; leave it empty on screens that only
    belong to the destination (a receipt, the reminders)."""
    return {
        "sections": SECTIONS,
        "current": current,
        "receivable": [
            (key, label, reverse(url_name) + query) for key, label, url_name, query in RECEIVABLE
        ]
        if current == "receber" and receivable
        else (),
        "receivable_current": "todas" if receivable == "sem-comprovante" else receivable,
        "receipts_to_review": review_queue().count(),
    }
