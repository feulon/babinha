"""Bootstrap the first account. Every other player signs up naming who referred them."""

import os
from getpass import getpass

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.core.validators import validate_email

from accounts.models import Player
from accounts.phone import format_phone
from accounts.validators import reject_blocked_password

PASSWORD_ENV = "BABA_BOOTSTRAP_PASSWORD"


class Command(BaseCommand):
    help = (
        "Cria a conta do presidente (primeira conta, sem indicação). "
        f"A senha vem de {PASSWORD_ENV} ou é pedida no terminal."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--telefone", required=True, help="celular com DDD, é o login")
        parser.add_argument("--email", required=True, help="e-mail, para recuperar a senha")
        parser.add_argument("--nome", required=True)
        parser.add_argument("--apelido", help="como aparece nas listas; sem ele, usa o nome")

    def handle(self, *args: object, **options: str) -> None:
        if Player.objects.filter(is_presidente=True).exists():
            raise CommandError("Já existe um presidente.")
        email = options["email"].strip().lower()
        try:
            validate_email(email)
        except ValidationError as exc:
            raise CommandError("Informe um e-mail válido em --email.") from exc
        password = os.environ.get(PASSWORD_ENV) or getpass("Senha (4 caracteres): ")
        try:
            password_validation.validate_password(password)
            reject_blocked_password(password)
            player = Player.objects.create_player(
                phone=options["telefone"],
                email=email,
                password=password,
                name=options["nome"],
                nickname=options["apelido"] or options["nome"],
                referred_by=None,
                is_presidente=True,
            )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Presidente {player.display_name} ({format_phone(player.phone)}) criado."
            )
        )
