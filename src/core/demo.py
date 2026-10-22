"""Demo mode (BABA_DEMO): the game day schedule is released, so every Sunday process can be tried
on any day and hour without waiting for the calendar.

Only the clock rules of the game day are released: when the list opens and closes, when the
teams come out, when presence, goals and the end of a match can be recorded, when the game can
be finished and charged, and when the Bola de Ouro can be voted. Everything else keeps working
as in the real app: capacity, priorities, the waitlist, permissions, amounts and fines. Instead
of the calendar, the state of the Sunday drives the phases that follow the time:

- the free period (priorities, no fine for dropping out) lasts until the teams are confirmed;
- the list closes when the diretoria finishes the game;
- the Bola de Ouro opens when the diretoria finishes the Sunday and closes when everyone who
  played has voted.

Every screen shows a banner while the mode is on (see `core.context_processors.demo_banner`) and
the app refuses to start in production with it on (see `baba.production_checks`).
"""

from django.conf import settings

BANNER = "Demonstração: os horários do dia do jogo estão liberados"


def is_on() -> bool:
    return settings.BABA_DEMO
