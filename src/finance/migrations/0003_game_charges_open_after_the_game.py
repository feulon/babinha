from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.db import migrations
from django.utils import timezone

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
CHARGES_OPEN_AT = time(12, 30)
NOT_PAID_YET = ("pendente", "recusada")


def open_game_charges_after_the_game(apps, schema_editor):
    """Sunday fees only exist once the game is over.

    Games already past 12h30 keep their charges and are marked finished. Games still to come lose
    the unpaid fees created at sign-up (they are cancelled, never deleted, and charged again when
    the game ends). Fees with a receipt under review or already paid are left alone.
    """
    GameDay = apps.get_model("games", "GameDay")
    Charge = apps.get_model("finance", "Charge")
    now = timezone.now()
    for game_day in GameDay.objects.filter(cancelled_at__isnull=True):
        opens = datetime.combine(game_day.date, CHARGES_OPEN_AT, tzinfo=SAO_PAULO)
        if opens <= now:
            game_day.finished_at = opens
            game_day.save(update_fields=["finished_at"])
            continue
        Charge.objects.filter(
            game_day=game_day, kind="jogo", status__in=NOT_PAID_YET
        ).update(
            status="cancelada",
            cancelled_at=now,
            cancel_reason="Cobrança só depois do fim do jogo",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0002_receipt_superseded_status"),
        ("games", "0002_game_day_finished"),
    ]

    operations = [
        migrations.RunPython(open_game_charges_after_the_game, migrations.RunPython.noop),
    ]
