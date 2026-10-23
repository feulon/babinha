"""Erasing a player for good (a LGPD request), not just deactivating them.

Every reference to the player is handled by one of three lists, so nothing is left pointing at
a removed account and the database never refuses the delete:

- DELETED: rows that are the player's own data (list entries, goals, votes, charges...), in an
  order that respects the foreign keys between them;
- CLEARED: references that can be emptied without losing anyone else's data (an assist on a
  teammate's goal, the goalkeeper of a match, the "indicado por" of whoever they referred);
- HANDED_OVER: what they did as diretoria on other people's data (expenses, goals typed in,
  receipts reviewed...). Those rows are the club's, so they stay and pass to the presidente.

`erase` runs inside the caller's transaction and returns the files to drop from storage only
after it commits: a file is never deleted for a database change that was rolled back.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.db import models

from accounts.models import MonthlyEnrollment, Player, ThrottleEvent
from community.models import Ballot, PlaceTally, Season
from core import clock
from core.models import ClubSettings, Feedback
from finance.models import Charge, Expense, PaymentReminder, Receipt
from fines.models import Fine
from games.domain.types import EntryStatus
from games.models import GameDay, ListEntry
from matchday.models import (
    Goal,
    Lineup,
    LineupSpot,
    Match,
    MatchPlayer,
    RookieRelease,
    TogetherGroup,
    TogetherMember,
)


@dataclass(frozen=True)
class Reference:
    label: str
    model: type[models.Model]
    lookup: str  # the field, or a path to the player, that points at them

    def rows(self, player: Player) -> models.QuerySet:
        return self.model._default_manager.filter(**{self.lookup: player})


DELETED = (
    Reference("comprovantes", Receipt, "charge__player"),
    Reference("cobranças", Charge, "player"),
    Reference("multas", Fine, "player"),
    Reference("gols marcados", Goal, "scorer"),
    Reference("jogadores nas partidas", MatchPlayer, "player"),
    Reference("jogadores nos times", LineupSpot, "player"),
    Reference("inscrições nas listas", ListEntry, "player"),
    Reference("grupos que jogam juntos", TogetherMember, "player"),
    Reference("estreante liberado", RookieRelease, "player"),
    Reference("votos dados (registro de que votou)", Ballot, "voter"),
    Reference("votos recebidos na Bola de Ouro", PlaceTally, "nominee"),
    Reference("adesões ao plano mensal", MonthlyEnrollment, "player"),
    Reference("avisos de pagamento", PaymentReminder, "player"),
    Reference("mensagens enviadas pelo app", Feedback, "author"),
)

CLEARED = (
    Reference("assistências em gols de outros", Goal, "assist"),
    Reference("partidas como goleiro do time 1", Match, "home_goalkeeper"),
    Reference("partidas como goleiro do time 2", Match, "away_goalkeeper"),
    Reference("jogadores indicados por ele", Player, "referred_by"),
)

HANDED_OVER = (
    Reference("domingos cancelados", GameDay, "cancelled_by"),
    Reference("domingos finalizados", GameDay, "finished_by"),
    Reference("presenças marcadas", ListEntry, "attendance_marked_by"),
    Reference("times sorteados", Lineup, "drawn_by"),
    Reference("times confirmados", Lineup, "confirmed_by"),
    Reference("partidas encerradas", Match, "finished_by"),
    Reference("gols lançados", Goal, "created_by"),
    Reference("grupos que jogam juntos criados", TogetherGroup, "created_by"),
    Reference("estreantes liberados", RookieRelease, "released_by"),
    Reference("temporadas encerradas", Season, "ended_by"),
    Reference("adesões mensais registradas", MonthlyEnrollment, "enrolled_by"),
    Reference("cobranças canceladas ou dispensadas", Charge, "cancelled_by"),
    Reference("cobranças marcadas como pagas", Charge, "marked_paid_by"),
    Reference("comprovantes enviados por outros", Receipt, "submitted_by"),
    Reference("comprovantes analisados", Receipt, "reviewed_by"),
    Reference("multas resolvidas", Fine, "resolved_by"),
    Reference("despesas lançadas", Expense, "created_by"),
    Reference("despesas alteradas", Expense, "updated_by"),
    Reference("avisos de pagamento enviados", PaymentReminder, "sent_by"),
    Reference("chave Pix trocada", ClubSettings, "pix_key_changed_by"),
    Reference("valores alterados", ClubSettings, "updated_by"),
)

# Removed by the database together with the player (Django's own tables).
CASCADED = (LogEntry._meta.get_field("user"),)


class ErasureRefused(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Count:
    label: str
    rows: int


@dataclass(frozen=True)
class Erasure:
    player_id: int
    deleted: tuple[Count, ...]
    cleared: tuple[Count, ...]
    handed_over: tuple[Count, ...]
    files: tuple[str, ...]  # storage names to delete once the transaction commits


def check_can_erase(player: Player) -> None:
    """Refuse the presidente and anyone still on the list or waitlist of a coming Sunday."""
    if player.is_presidente:
        raise ErasureRefused("Não é possível apagar o presidente.")
    on_a_list = ListEntry.objects.filter(
        player=player,
        status__in=(EntryStatus.LISTA.value, EntryStatus.ESPERA.value),
        game_day__date__gte=clock.now().astimezone(clock.SAO_PAULO).date(),
        game_day__finished_at=None,
        game_day__cancelled_at=None,
    )
    if on_a_list.exists():
        raise ErasureRefused(
            "O jogador está na lista ou na espera de um domingo que ainda vai acontecer. "
            "Tire ele da lista pelo app antes (assim a espera anda e a cobrança é cancelada)."
        )


def files_of(player: Player) -> tuple[str, ...]:
    """The profile photo and every receipt sent for the player's charges."""
    receipts = Receipt.objects.filter(charge__player=player).values_list("file", flat=True)
    return tuple(name for name in (player.photo, *receipts) if name)


def erase(player: Player, heir: Player | None) -> Erasure:
    """Delete the player and their data. Run it inside transaction.atomic().

    `heir` (the presidente) takes over what the player did as diretoria. Without one, a player
    who did any of that cannot be erased.
    """
    check_can_erase(player)
    player_id = player.pk
    files = files_of(player)
    deleted = _apply(DELETED, player, lambda ref, rows: rows.delete()[0])
    cleared = _apply(CLEARED, player, lambda ref, rows: rows.update(**{ref.lookup: None}))
    handed_over = _hand_over(player, heir)
    _forget_logins(player)
    player.delete()
    return Erasure(player_id, deleted, cleared, handed_over, files)


def _apply(
    references: Sequence[Reference],
    player: Player,
    change: Callable[[Reference, models.QuerySet], int],
) -> tuple[Count, ...]:
    return tuple(Count(ref.label, change(ref, ref.rows(player))) for ref in references)


def _hand_over(player: Player, heir: Player | None) -> tuple[Count, ...]:
    counts = []
    for ref in HANDED_OVER:
        rows = ref.rows(player)
        if heir is None and rows.exists():
            raise ErasureRefused(
                "O jogador registrou coisas como diretoria e não há presidente para assumi-las."
            )
        counts.append(Count(ref.label, rows.update(**{ref.lookup: heir})))
    return tuple(counts)


def _forget_logins(player: Player) -> None:
    """Sessions, login lockouts and admin history that name the player."""
    key = str(player.pk)
    stale = [s.pk for s in Session.objects.all() if s.get_decoded().get("_auth_user_id") == key]
    Session.objects.filter(pk__in=stale).delete()
    account = f"player:{player.pk}"
    ThrottleEvent.objects.filter(
        models.Q(key=account) | models.Q(key__startswith=f"{account}|")
    ).delete()
    LogEntry.objects.filter(
        content_type=ContentType.objects.get_for_model(Player), object_id=key
    ).delete()
