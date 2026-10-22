"""Testing clock override: BABA_FAKE_NOW makes the app believe it is another moment.

The weekly list follows real Sao Paulo time, so trying the list, waitlist and dropout flows
would otherwise depend on the calendar. The override is honored only with DJANGO_DEBUG turned
on, never in a regular production deploy, and every page shows a banner while it is active.
"""

import logging
from datetime import datetime

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from core import clock

logger = logging.getLogger(__name__)


def parse_fake_now(raw: str) -> datetime | None:
    """`2026-10-15T20:00` (Sao Paulo time) or with an explicit offset. Empty means no override."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"BABA_FAKE_NOW must be an ISO date and time like 2026-10-15T20:00, got {raw!r}"
        ) from exc
    return value.replace(tzinfo=clock.SAO_PAULO) if value.tzinfo is None else value


def fake_start(*, raw: str, debug: bool) -> datetime | None:
    """The moment the clock should start at, or None when the override does not apply."""
    if not raw.strip():
        return None
    if not debug:
        logger.warning("BABA_FAKE_NOW ignored: it needs DJANGO_DEBUG turned on.")
        return None
    return parse_fake_now(raw)


def install_from_settings() -> None:
    """Called once at startup. Leaves the real clock in place unless the override applies."""
    start = fake_start(raw=settings.BABA_FAKE_NOW, debug=settings.DEBUG)
    if start is None:
        return
    clock.set_clock(clock.ShiftedClock(start))
    logger.warning("Fake clock active: the app starts at %s.", start.isoformat())


def is_active() -> bool:
    return isinstance(clock.get_clock(), clock.ShiftedClock)
