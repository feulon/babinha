"""Build the Sunday situations of the demo, to try every Sunday process without the calendar."""

import logging

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser

from accounts.models import Player
from accounts.phone import format_phone
from demo import seed
from demo.players import DEMO_EMAIL_DOMAIN, DEMO_PASSWORD, PLAYERS, is_demo_email
from finance.models import Receipt

logger = logging.getLogger(__name__)

DEFAULT_SCENARIO = "lista"


class Command(BaseCommand):
    help = (
        "Monta uma situação de domingo com jogadores fictícios, usando os mesmos serviços das "
        "telas. Só funciona com BABA_DEMO ligado. Use --listar para ver as situações e as contas."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "cenario",
            nargs="?",
            default=DEFAULT_SCENARIO,
            help=f"A situação a montar (padrão: {DEFAULT_SCENARIO}). Veja --listar.",
        )
        parser.add_argument(
            "--listar",
            action="store_true",
            help="Mostra as situações e as contas de teste, sem alterar nada.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Apaga todos os dados do banco e monta a situação do zero.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Junto com --reset, apaga também contas que não são de demonstração.",
        )
        parser.add_argument(
            "--se-vazio",
            action="store_true",
            help="Monta só se o banco ainda não tem jogadores (usado ao subir o Docker).",
        )

    def handle(
        self,
        *args: object,
        cenario: str,
        listar: bool,
        reset: bool,
        force: bool,
        se_vazio: bool,
        **options: object,
    ) -> None:
        if listar:
            self._list()
            return
        if not settings.BABA_DEMO:
            raise CommandError(
                "A demonstração está desligada. Suba o app com BABA_DEMO=true "
                "(docker compose -f compose.demo.yaml up --build) e rode o comando de lá."
            )
        if force and not reset:
            raise CommandError("--force só vale junto com --reset.")
        if cenario not in seed.BY_KEY:
            names = ", ".join(seed.BY_KEY)
            raise CommandError(f"Situação desconhecida: {cenario!r}. As opções: {names}.")
        if se_vazio and Player.objects.exists():
            self.stdout.write("O banco já tem dados: nada a montar.")
            return
        self._check_database(reset=reset, force=force)
        if reset:
            self._wipe()
        self._report(seed.seed(cenario))

    def _list(self) -> None:
        self.stdout.write("Situações (use: manage.py demo <situação> --reset):\n")
        for scenario in seed.SCENARIOS:
            self.stdout.write(f"  {scenario.key:<26} {scenario.title}")
        self.stdout.write(
            f"\nContas de teste (senha {DEMO_PASSWORD}; entre com o celular ou o e-mail):"
        )
        for spec in PLAYERS:
            self.stdout.write(f"  {format_phone(spec.phone):<16} {spec.email:<26} {spec.nickname}")

    def _check_database(self, *, reset: bool, force: bool) -> None:
        emails = list(Player.objects.values_list("email", flat=True))
        real = [email for email in emails if not is_demo_email(email)]
        if real and not (reset and force):
            raise CommandError(
                f"Este banco tem {len(real)} conta(s) que não são de demonstração (fora de "
                f"@{DEMO_EMAIL_DOMAIN}). O comando não apaga dados reais. Se quer mesmo apagar "
                "TUDO e montar a demonstração, rode com --reset --force."
            )
        if emails and not reset:
            raise CommandError(
                "Já existem dados de demonstração. Rode com --reset para apagar e montar de novo."
            )

    def _wipe(self) -> None:
        """Empty every table, so models added later are covered too. Receipt files go first:
        they live in the bucket, outside the database."""
        storage_errors = 0
        for receipt in Receipt.objects.only("file"):
            try:
                receipt.file.storage.delete(receipt.file.name)
            except Exception:
                storage_errors += 1
                logger.exception("Could not delete receipt file %s", receipt.file.name)
        if storage_errors:
            self.stderr.write(
                f"Aviso: {storage_errors} arquivo(s) de comprovante não puderam ser apagados."
            )
        call_command("flush", interactive=False, verbosity=0)
        self.stdout.write("Banco apagado.")

    def _report(self, result: seed.SeedResult) -> None:
        self.stdout.write(
            self.style.SUCCESS(
                f"Situação '{result.scenario.key}' montada para o domingo "
                f"{result.sunday:%d/%m/%Y}: {result.scenario.title}."
            )
        )
        self.stdout.write(
            f"\nTodas as contas usam a senha {DEMO_PASSWORD}. Entre com o celular ou o e-mail."
            " Para começar:"
        )
        for login in result.scenario.logins:
            player = result.players[login.key]
            phone = format_phone(player.phone)
            self.stdout.write(
                f"  {phone:<16} {player.email:<26} {player.display_name} - {login.role}"
            )
