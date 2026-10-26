"""The weekly calendar of the Sunday game, in America/Sao_Paulo time."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from core import demo
from core.clock import SAO_PAULO
from games.domain.types import PlayerType

MONDAY = 0  # date.weekday()
SUNDAY = 6  # date.weekday()
GAME_STARTS_AT = time(10, 0)
GAME_ENDS_AT = time(12, 0)
# The teams come out about an hour before kickoff.
TEAMS_OUT_AT = time(9, 0)
# The pelada wraps up (and the Sunday fee falls due) half an hour after the last whistle.
CHARGES_OPEN_AT = time(12, 30)
MATCHDAY_FOCUS_ENDS_MONDAY_AT = time(8, 0)
AGREGADO_OPENS_AT = time(20, 0)
DAYS_FROM_MONDAY = 6
DAYS_FROM_WEDNESDAY = 4


def _at(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=SAO_PAULO)


@dataclass(frozen=True)
class WeekSchedule:
    """Key moments of the week that ends on `sunday`.

    `free_until` is Sunday 00:00: everything up to Saturday 23h59 is the free period in which
    dropping out or declining a promotion costs nothing and priority rules apply.
    """

    sunday: date
    list_opens_at: time

    def __post_init__(self) -> None:
        if self.sunday.weekday() != SUNDAY:
            raise ValueError(f"{self.sunday} is not a Sunday")

    @property
    def opens_main(self) -> datetime:
        """Monday morning: list opens for mensalistas and avulsos."""
        return _at(self.sunday - timedelta(days=DAYS_FROM_MONDAY), self.list_opens_at)

    @property
    def opens_agregado(self) -> datetime:
        """Wednesday 20h: list opens for agregados."""
        return _at(self.sunday - timedelta(days=DAYS_FROM_WEDNESDAY), AGREGADO_OPENS_AT)

    @property
    def free_until(self) -> datetime:
        return _at(self.sunday, time(0, 0))

    @property
    def last_free_minute(self) -> datetime:
        """Saturday 23h59, the last minute of the free period, for display."""
        return self.free_until - timedelta(minutes=1)

    @property
    def starts(self) -> datetime:
        return _at(self.sunday, GAME_STARTS_AT)

    @property
    def ends(self) -> datetime:
        return _at(self.sunday, GAME_ENDS_AT)

    @property
    def charges_open(self) -> datetime:
        """Sunday 12h30: the single fee of the game becomes payable."""
        return _at(self.sunday, CHARGES_OPEN_AT)

    @property
    def vote_closes(self) -> datetime:
        """Monday after the game, when the next list opens: the Bola de Ouro result comes out."""
        return _at(self.sunday + timedelta(days=1), self.list_opens_at)

    def opens_for(self, player_type: PlayerType) -> datetime:
        if player_type == PlayerType.AGREGADO:
            return self.opens_agregado
        return self.opens_main


def upcoming_sunday(now: datetime) -> date:
    """The Sunday whose game has not ended yet: this week's until Sunday 12h, then the next.

    In demo mode the game never ends by the clock, so the Sunday of the demo stays put all day."""
    local = now.astimezone(SAO_PAULO)
    today = local.date()
    sunday = today + timedelta(days=(SUNDAY - today.weekday()) % 7)
    if sunday == today and local.time() >= GAME_ENDS_AT and not demo.is_on():
        sunday += timedelta(days=7)
    return sunday


def matchday_sunday(now: datetime) -> date:
    """The Sunday the game day screens (Jogos and the diretoria's day tabs) stay on.

    Unlike `upcoming_sunday`, today's Sunday keeps the focus after the game ends, until Monday
    08h, so the result, late corrections and the Bola de Ouro stay one tap away.
    """
    local = now.astimezone(SAO_PAULO)
    today = local.date()
    if demo.is_on():
        return upcoming_sunday(now)
    if today.weekday() == SUNDAY:
        return today
    if today.weekday() == MONDAY and local.time() < MATCHDAY_FOCUS_ENDS_MONDAY_AT:
        return today - timedelta(days=1)
    return upcoming_sunday(now)


def follow_sunday(now: datetime) -> date:
    """The Sunday of the player's "Acompanhar o domingo" screen.

    Like `matchday_sunday`, except that the Sunday just played stays all Monday: the Bola de
    Ouro result only comes out when the next list opens (Monday morning), and it must be there
    to be read, not already replaced by the next Sunday."""
    local = now.astimezone(SAO_PAULO)
    if local.weekday() == MONDAY and not demo.is_on():
        return local.date() - timedelta(days=1)
    return matchday_sunday(now)


def last_game_date(now: datetime) -> date:
    """The latest Sunday whose game can have been played: today, or in demo mode the Sunday of
    the demo itself, which is usually still to come."""
    return upcoming_sunday(now) if demo.is_on() else now.date()


def sunday_arrived(sunday: date, now: datetime) -> bool:
    """Whether the day of the game has come (from 00h of the Sunday, Sao Paulo time). Demo mode
    releases the calendar, so the Sunday of the demo has always arrived."""
    return demo.is_on() or now.astimezone(SAO_PAULO).date() >= sunday


def teams_out(now: datetime) -> bool:
    """Whether it is game day and the teams are out: Sunday from one hour before kickoff on."""
    if demo.is_on():
        return True
    local = now.astimezone(SAO_PAULO)
    return local.weekday() == SUNDAY and local.time() >= TEAMS_OUT_AT


def sunday_on_or_after(day: date) -> date:
    return day + timedelta(days=(SUNDAY - day.weekday()) % 7)


def is_last_sunday_of_month(sunday: date) -> bool:
    return (sunday + timedelta(days=7)).month != sunday.month


def plan_month(today: date, *, today_ended: bool) -> date:
    """The month of the monthly plan on `today`: the month of the next Sunday that has not ended.

    A Sunday belongs to the month of its date and ends when the diretoria finishes it
    (`today_ended`, about today's Sunday) or, at the latest, at 00:00 of the Monday after. So a
    month's mensalistas hold through its last Sunday, and joining the next month opens right
    after it, before the list of that month's first Sunday opens.
    """
    sunday = sunday_on_or_after(today)
    if sunday == today and today_ended:
        sunday += timedelta(days=7)
    return sunday.replace(day=1)


def plan_opens(month: date) -> datetime:
    """When joining `month` opens at the latest: 00:00 of the Monday after the last Sunday of the
    month before, which is the Monday of the week of the month's first Sunday."""
    first_sunday = sunday_on_or_after(month.replace(day=1))
    return _at(first_sunday - timedelta(days=DAYS_FROM_MONDAY), time(0, 0))
