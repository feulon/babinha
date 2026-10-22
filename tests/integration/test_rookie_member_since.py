"""A first-timer is someone new to the group: "No baba desde" before the app counts as a veteran."""

from collections.abc import Callable
from datetime import date

import pytest

from accounts.models import Player
from games.models import GameDay
from matchday import services
from matchday.grouping import first_timers
from tests.conftest import Sunday
from tests.integration.test_team_rules import LAST_SUNDAY, invite, played_before

pytestmark = pytest.mark.django_db


@pytest.fixture
def sunday(fill_sunday: Callable[..., Sunday]) -> Sunday:
    return fill_sunday(line=6, goalkeepers=0)


def ids(sunday: Sunday) -> list[int]:
    return [p.pk for p in sunday.line]


def test_everybody_is_new_when_nobody_has_history(sunday: Sunday) -> None:
    assert first_timers(sunday.game_day, ids(sunday)) == set(ids(sunday))


def test_member_since_before_the_first_sunday_of_the_app_is_a_veteran(sunday: Sunday) -> None:
    GameDay.objects.get_or_create(date=LAST_SUNDAY)  # the app's first Sunday
    old, recent, unknown, *_ = sunday.line
    Player.objects.filter(pk=old.pk).update(member_since=date(2025, 1, 1))
    Player.objects.filter(pk=recent.pk).update(member_since=LAST_SUNDAY)

    rookies = first_timers(sunday.game_day, ids(sunday))

    assert old.pk not in rookies
    assert {recent.pk, unknown.pk} <= rookies


def test_presence_on_an_earlier_sunday_still_makes_a_veteran(sunday: Sunday) -> None:
    played_before(sunday.line[0])
    assert sunday.line[0].pk not in first_timers(sunday.game_day, ids(sunday))


def test_the_rules_step_has_no_bond_for_a_veteran_the_diretoria_dated(sunday: Sunday) -> None:
    newcomer, veteran = sunday.line[:2]
    invite(veteran, newcomer)

    def rows() -> list[int]:
        return [r.player.pk for r in services.grouping_of(sunday.game_day).rookie_rows]

    assert veteran.pk in rows()

    Player.objects.filter(pk=veteran.pk).update(member_since=date(2020, 1, 1))

    assert veteran.pk not in rows()
