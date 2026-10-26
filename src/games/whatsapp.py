"""The Sunday list as plain text to forward on WhatsApp, in the order the app itself shows it."""

from datetime import date

from core.models import ClubSettings
from games.domain.roster import Entry
from games.domain.types import PlayerType, Position
from games.models import GameDay
from games.services import context_for, entry_rows, schedule_for, to_roster

GOALKEEPER_MARK = "🧤"
SECTIONS = (
    (PlayerType.MENSALISTA, "Mensalistas ⚽️"),
    (PlayerType.AVULSO, "AVULSOS"),
    (PlayerType.AGREGADO, "AGREGADOS 🐣"),
)


def whatsapp_list(sunday: date) -> str:
    """Header, then goalkeepers and each kind of line player in the app's order, then the waitlist.

    Reads the database only: a Sunday nobody looked at yet has no row and simply has no list.
    """
    game_day = GameDay.objects.filter(date=sunday).first() or GameDay(date=sunday)
    rows = entry_rows(game_day) if game_day.pk else []
    names = {row.player_id: row.player.display_name for row in rows}
    roster = to_roster(rows)
    ctx = context_for(game_day)

    def listed(entries: tuple[Entry, ...], *, keeper_mark: bool = False) -> list[str]:
        mark = f" {GOALKEEPER_MARK}" if keeper_mark else ""
        return [f"{names[e.player_id]}{mark}" for e in entries]

    line = roster.confirmed(Position.LINHA)
    sections = [
        ("GOLEIROS 🧤", listed(roster.confirmed(Position.GOLEIRO), keeper_mark=True)),
        *(
            (title, listed(tuple(e for e in line if e.player_type == kind)))
            for kind, title in SECTIONS
        ),
        (
            "ESPERA",
            [
                *listed(
                    roster.waitlist(Position.GOLEIRO, in_free_period=ctx.in_free_period),
                    keeper_mark=True,
                ),
                *listed(roster.waitlist(Position.LINHA, in_free_period=ctx.in_free_period)),
            ],
        ),
    ]
    blocks = [_header(sunday)]
    if game_day.is_cancelled:
        blocks.append("Domingo cancelado pela diretoria.")
    blocks.extend(_numbered(title, people) for title, people in sections if people)
    if len(blocks) == 1:
        blocks.append("Ninguém na lista ainda.")
    return "\n\n".join(blocks)


def _header(sunday: date) -> str:
    """The app has no name for a Sunday: the title is the weekday and the date."""
    where = ClubSettings.load().venue_name.strip()
    starts = schedule_for(sunday).starts
    when = f"{starts:%Hh%M}" + (f" | {where}" if where else "")
    return f"Domingo ({sunday:%d/%m})\n{when}"


def _numbered(title: str, people: list[str]) -> str:
    return "\n".join([title, *(f"{n}. {person}" for n, person in enumerate(people, start=1))])
