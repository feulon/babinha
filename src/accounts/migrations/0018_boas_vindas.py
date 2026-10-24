from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def mark_existing_as_seen(apps, schema_editor):
    """Nobody who is already in the app sees the welcome: players have the player one behind
    them, and whoever is on the diretoria already knows the Painel."""
    Player = apps.get_model("accounts", "Player")
    now = timezone.now()
    Player.objects.filter(welcome_seen_at__isnull=True).update(welcome_seen_at=now)
    Player.objects.filter(Q(is_diretoria=True) | Q(is_presidente=True)).update(
        board_welcome_seen_at=now
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0017_player_is_admin_and_sunday_tutorial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="player",
            old_name="rules_seen_at",
            new_name="welcome_seen_at",
        ),
        migrations.AlterField(
            model_name="player",
            name="welcome_seen_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Quando abriu as boas-vindas do jogador. Quem já existia entra como visto.",
                null=True,
                verbose_name="boas-vindas vistas em",
            ),
        ),
        migrations.AddField(
            model_name="player",
            name="board_welcome_seen_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "Quando abriu as boas-vindas da diretoria, na primeira visita ao Painel. "
                    "Quem já era da diretoria entra como visto."
                ),
                null=True,
                verbose_name="boas-vindas da diretoria vistas em",
            ),
        ),
        migrations.RunPython(mark_existing_as_seen, migrations.RunPython.noop),
    ]
