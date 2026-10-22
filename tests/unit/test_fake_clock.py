from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from core import clock, fake_clock
from core.clock import SAO_PAULO, ShiftedClock, SystemClock
from core.context_processors import fake_clock_banner

FAKE = "2026-10-15T20:00"
FAKE_AT = datetime(2026, 10, 15, 20, 0, tzinfo=SAO_PAULO)


@pytest.fixture(autouse=True)
def restore_clock() -> Iterator[None]:
    with clock.use_clock(SystemClock()):
        yield


class TestFakeStart:
    def test_ignored_outside_debug(self) -> None:
        assert fake_clock.fake_start(raw=FAKE, debug=False) is None

    def test_honored_with_debug(self) -> None:
        assert fake_clock.fake_start(raw=FAKE, debug=True) == FAKE_AT

    def test_empty_means_no_override(self) -> None:
        assert fake_clock.fake_start(raw="  ", debug=True) is None

    def test_explicit_offset_is_kept(self) -> None:
        start = fake_clock.fake_start(raw="2026-10-15T23:00+00:00", debug=True)
        assert start == FAKE_AT
        assert start.tzinfo == UTC

    def test_invalid_value_fails_fast(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="BABA_FAKE_NOW"):
            fake_clock.fake_start(raw="quinta 20h", debug=True)

    def test_invalid_value_is_harmless_when_ignored(self) -> None:
        assert fake_clock.fake_start(raw="quinta 20h", debug=False) is None


class TestInstall:
    @override_settings(DEBUG=False, BABA_FAKE_NOW=FAKE)
    def test_production_keeps_the_real_clock(self) -> None:
        fake_clock.install_from_settings()
        assert isinstance(clock.get_clock(), SystemClock)
        assert not fake_clock.is_active()
        assert fake_clock_banner(None)["fake_now"] is None

    @override_settings(DEBUG=True, BABA_FAKE_NOW=FAKE)
    def test_debug_installs_a_running_fake_clock(self) -> None:
        fake_clock.install_from_settings()
        assert fake_clock.is_active()
        now = clock.now()
        assert FAKE_AT <= now < FAKE_AT + timedelta(minutes=1)
        assert fake_clock_banner(None)["fake_now"] >= now


def test_shifted_clock_moves_with_real_time() -> None:
    shifted = ShiftedClock(FAKE_AT)
    first = shifted.now()
    assert shifted.now() >= first
    assert first.date() == FAKE_AT.date()


def test_shifted_clock_needs_an_aware_datetime() -> None:
    with pytest.raises(ValueError, match="aware"):
        ShiftedClock(datetime(2026, 10, 15, 20, 0))
