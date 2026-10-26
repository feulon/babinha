from datetime import datetime, time
from zoneinfo import ZoneInfo

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone

KICKOFF = time(10, 0)
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def start_the_sundays_already_under_way(apps, schema_editor):
    """Until now the game started by itself at 10h. Every Sunday whose 10h has passed keeps the
    presence, goals and finish it already had, as if the diretoria had started it then."""
    GameDay = apps.get_model("games", "GameDay")
    now = timezone.now()
    for game_day in GameDay.objects.filter(started_at__isnull=True, date__lte=now.date()):
        kickoff = datetime.combine(game_day.date, KICKOFF, tzinfo=SAO_PAULO)
        if kickoff <= now:
            game_day.started_at = kickoff
            game_day.save(update_fields=["started_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("games", "0004_listentry_board_rank"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="gameday",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="iniciado em"),
        ),
        migrations.AddField(
            model_name="gameday",
            name="started_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
                verbose_name="iniciado por",
            ),
        ),
        migrations.RunPython(start_the_sundays_already_under_way, migrations.RunPython.noop),
    ]
