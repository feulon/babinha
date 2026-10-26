"""Print the Sunday list as plain text for WhatsApp, straight from the database."""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError, CommandParser

from core import clock
from games.domain.schedule import SUNDAY, upcoming_sunday
from games.whatsapp import whatsapp_list


class Command(BaseCommand):
    help = (
        "Mostra a lista do próximo domingo em texto puro, na ordem do app, para colar no "
        "WhatsApp. Lê o banco direto: não depende do site abrir."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--data", help="outro domingo, no formato DD/MM/AAAA (padrão: o próximo domingo)"
        )

    def handle(self, *args: object, **options: object) -> None:
        raw = options["data"]
        if raw is None:
            sunday = upcoming_sunday(clock.now())
        else:
            try:
                sunday = datetime.strptime(str(raw).strip(), "%d/%m/%Y").date()
            except ValueError as exc:
                raise CommandError(f"Data inválida: {raw}. Use DD/MM/AAAA.") from exc
            if sunday.weekday() != SUNDAY:
                raise CommandError(f"{sunday:%d/%m/%Y} não é um domingo.")
        self.stdout.write(whatsapp_list(sunday))
