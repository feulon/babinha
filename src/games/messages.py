"""Turn engine events into messages for the player who acted or the diretoria."""

from django.contrib import messages

from accounts.models import Player
from games.domain import roster as engine
from games.services import ListResult

Message = tuple[int, str]  # (django.contrib.messages level, text)


def for_actor(result: ListResult, player_id: int) -> list[Message]:
    lines: list[Message] = []
    for event in result.events:
        match event:
            case engine.Confirmed(player_id=pid) if pid == player_id:
                lines.append((messages.SUCCESS, "Você está na lista deste domingo!"))
            case engine.Waitlisted(player_id=pid) if pid == player_id:
                lines.append(
                    (messages.WARNING, "A lista está cheia. Você entrou na fila de espera.")
                )
            case engine.Bumped(by_player_id=pid) if pid == player_id:
                lines.append(
                    (
                        messages.INFO,
                        "Você entrou pela prioridade e o último a entrar foi para a espera.",
                    )
                )
            case engine.Dropped(player_id=pid, was_confirmed=True) if pid == player_id:
                lines.append((messages.SUCCESS, "Você saiu da lista."))
            case engine.Dropped(player_id=pid, was_confirmed=False) if pid == player_id:
                lines.append((messages.SUCCESS, "Você saiu da fila de espera."))
            case _:
                pass
    if result.fines:
        lines.append(
            (
                messages.WARNING,
                "Como foi depois do prazo de sábado 23h59, uma multa foi registrada.",
            )
        )
    return lines


def for_board(result: ListResult, name: str) -> list[Message]:
    lines: list[Message] = []
    for event in result.events:
        match event:
            case engine.Confirmed():
                lines.append((messages.SUCCESS, f"{name} entrou na lista."))
            case engine.Waitlisted():
                lines.append((messages.WARNING, f"Lista cheia: {name} entrou na espera."))
            case engine.Bumped(player_id=pid):
                lines.append(
                    (
                        messages.INFO,
                        f"{_name_of(pid)} (o último a entrar) foi para a espera por prioridade.",
                    )
                )
            case engine.Dropped():
                lines.append((messages.SUCCESS, f"{name} foi retirado."))
            case engine.Promoted():
                lines.append((messages.INFO, "A vaga foi preenchida pela fila de espera."))
            case _:
                pass
    return lines


def _name_of(player_id: int) -> str:
    return Player.objects.values_list("name", flat=True).get(pk=player_id)
