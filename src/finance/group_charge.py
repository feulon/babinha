"""One message for the WhatsApp group, tagging every player who still owes."""

from collections.abc import Iterable
from decimal import Decimal
from urllib.parse import quote

from accounts.phone import COUNTRY_CODE, is_valid_phone
from core.models import ZERO
from core.templatetags.money import brl
from finance.reminders import Debtor
from finance.selectors import PixInfo

TITLE = "*Babinha de Domingo: pagamentos pendentes*"


def mention(phone: str | None) -> str:
    """@ plus country code, area code and number, digits only; "" when there is no valid phone."""
    if not phone or not is_valid_phone(phone):
        return ""
    return f"@{COUNTRY_CODE}{phone}"


def _line(debtor: Debtor) -> str:
    reasons = ", ".join(row.charge.description for row in debtor.rows)
    tag = mention(debtor.player.phone)
    who = f"{tag} {debtor.player.display_name}" if tag else debtor.player.display_name
    return f"{who} - {reasons} - {brl(debtor.total)}"


def compose(debtors: Iterable[Debtor], pix: PixInfo) -> str:
    """The message, or "" when nobody owes."""
    owing = tuple(debtors)
    if not owing:
        return ""
    total: Decimal = sum((d.total for d in owing), ZERO)
    parts = [TITLE, "\n".join(_line(d) for d in owing), f"Total devido: {brl(total)}"]
    if pix.is_configured:
        # holder = f" (em nome de {pix.holder})" if pix.holder else ""
        holder = " (em nome de Matheus Rodrigues" if pix.holder else ""
        parts.append(f"Pix: {pix.key}{holder}")
    return "\n\n".join(parts)


def whatsapp_share_url(message: str) -> str:
    return f"https://wa.me/?text={quote(message)}"
