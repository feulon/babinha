"""Turn the hidden admin role on or off for one account. There is no screen for this."""

from django.core.management.base import BaseCommand, CommandError, CommandParser

from accounts.models import Player


class Command(BaseCommand):
    help = (
        "Liga ou desliga o papel oculto de administrador de uma conta existente, pelo e-mail. "
        "O administrador tem os poderes do presidente sem aparecer como presidente."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--email", required=True, help="e-mail da conta")
        parser.add_argument(
            "--remover", action="store_true", help="desliga o papel em vez de ligar"
        )

    def handle(self, *args: object, **options: object) -> None:
        email = str(options["email"]).strip()
        try:
            player = Player.objects.get(email__iexact=email)
        except Player.DoesNotExist as exc:
            raise CommandError(f"Nenhuma conta com o e-mail {email}.") from exc
        enable = not options["remover"]
        if enable and player.is_presidente:
            raise CommandError("O presidente já tem todos os poderes. Use outra conta.")
        if player.is_admin == enable:
            state = "já é" if enable else "já não é"
            self.stdout.write(f"{player.display_name} {state} administrador. Nada mudou.")
            return
        player.is_admin = enable
        player.save(update_fields=["is_admin"])
        verb = "ligado" if enable else "desligado"
        self.stdout.write(self.style.SUCCESS(f"Administrador {verb} para {player.display_name}."))
