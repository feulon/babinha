"""Erase a player and all their data for good (LGPD). Simulates unless --confirmar is given."""

import logging
import sys

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from accounts import erasure, photos
from accounts.erasure_effects import effects
from accounts.models import Player
from accounts.phone import format_phone, normalize_phone
from core import clock

logger = logging.getLogger(__name__)


def is_interactive() -> bool:
    return sys.stdin.isatty()


def ask(prompt: str) -> str:
    return input(prompt)


class Command(BaseCommand):
    help = (
        "Apaga de verdade um jogador e todos os dados dele (pedido LGPD). Sem --confirmar só "
        "simula e mostra o que seria apagado."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("telefone", help="celular com DDD do jogador (o login)")
        parser.add_argument(
            "--confirmar",
            action="store_true",
            help="apaga de verdade, depois de digitar o celular de novo",
        )
        parser.add_argument(
            "--sim-tenho-certeza",
            action="store_true",
            help="permite --confirmar sem terminal interativo (para scripts)",
        )

    def handle(self, *args: object, **options: object) -> None:
        self.stdout.write("Antes de apagar de verdade, faça um backup: scripts/baba.sh backup.\n")
        self.stdout.flush()  # before any error, which goes to stderr
        phone = self._phone(str(options["telefone"]))
        player = self._player(phone)
        presidente = Player.objects.filter(is_presidente=True).first()
        simulation = self._simulate(player, presidente)
        self._report(player, simulation, effects(player))
        if not options["confirmar"]:
            self.stdout.write(
                "\nSimulação: nada foi apagado. Para apagar, rode de novo com --confirmar."
            )
            return
        self._confirm(phone, sure=bool(options["sim_tenho_certeza"]))
        result = self._erase(phone, presidente)
        logger.info("Jogador %s apagado em %s", result.player_id, clock.now().isoformat())
        self.stdout.write(self.style.SUCCESS(f"\nJogador {player.name} apagado."))
        self._delete_files(result.files)

    def _phone(self, raw: str) -> str:
        try:
            return normalize_phone(raw)
        except ValidationError as exc:
            raise CommandError(f"Nenhum jogador com o celular {raw}.") from exc

    def _player(self, phone: str) -> Player:
        player = Player.objects.filter(phone=phone).first()
        if player is None:
            raise CommandError(f"Nenhum jogador com o celular {format_phone(phone)}.")
        try:
            erasure.check_can_erase(player)
        except erasure.ErasureRefused as exc:
            raise CommandError(exc.message) from exc
        return player

    def _simulate(self, player: Player, presidente: Player | None) -> erasure.Erasure:
        """The real erasure, rolled back: the counts are exactly what --confirmar would do."""
        try:
            with transaction.atomic():
                result = erasure.erase(Player.objects.get(pk=player.pk), presidente)
                transaction.set_rollback(True)
        except erasure.ErasureRefused as exc:
            raise CommandError(exc.message) from exc
        return result

    def _report(self, player: Player, result: erasure.Erasure, changes: list[str]) -> None:
        write = self.stdout.write
        write(f"Jogador: {player.name} ({format_phone(player.phone)}), id {player.pk}.")
        write("\nLinhas apagadas:")
        for count in result.deleted:
            write(f"  {count.label}: {count.rows}")
        write(f"  arquivos no armazenamento (foto e comprovantes): {len(result.files)}")
        write("\nLigações esvaziadas (os registros ficam):")
        for count in result.cleared:
            write(f"  {count.label}: {count.rows}")
        handed = [count for count in result.handed_over if count.rows]
        if handed:
            write("\nFeito por ele como diretoria, passa para o presidente:")
            for count in handed:
                write(f"  {count.label}: {count.rows}")
        write("\nEfeitos no que fica:")
        for line in changes or ["nenhum placar, valor, ranking ou Bola de Ouro muda."]:
            write(f"  {line}")

    def _confirm(self, phone: str, *, sure: bool) -> None:
        if not is_interactive():
            if sure:
                return
            raise CommandError(
                "Sem terminal interativo não dá para confirmar digitando o celular. "
                "Em scripts, passe também --sim-tenho-certeza."
            )
        typed = ask("\nPara apagar, digite de novo o celular do jogador: ")
        try:
            matches = normalize_phone(typed) == phone
        except ValidationError:
            matches = False
        if not matches:
            raise CommandError("O celular digitado não confere. Nada foi apagado.")

    def _erase(self, phone: str, presidente: Player | None) -> erasure.Erasure:
        try:
            with transaction.atomic():
                player = Player.objects.select_for_update().get(phone=phone)
                return erasure.erase(player, presidente)
        except (Player.DoesNotExist, erasure.ErasureRefused) as exc:
            message = getattr(exc, "message", "O jogador não existe mais.")
            raise CommandError(f"{message} Nada foi apagado.") from exc

    def _delete_files(self, names: tuple[str, ...]) -> None:
        storage = photos.photo_storage()  # the private bucket of photos and receipts
        left = []
        for name in names:
            try:
                storage.delete(name)
            except Exception:
                logger.exception("Could not delete %s from storage", name)
                left.append(name)
        if left:
            listing = "\n".join(f"  {name}" for name in left)
            raise CommandError(
                f"O banco foi apagado, mas {len(left)} arquivos ficaram no armazenamento; "
                f"apague-os à mão:\n{listing}"
            )
        self.stdout.write(f"{len(names)} arquivos apagados do armazenamento.")
