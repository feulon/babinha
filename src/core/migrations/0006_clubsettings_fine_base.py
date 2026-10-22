from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models

DEFAULT_FINE_BASE = Decimal("50.00")


def keep_configured_first_fine(apps, schema_editor):
    """The old 1st-occurrence value becomes the base when it was configured; else the default."""
    ClubSettings = apps.get_model("core", "ClubSettings")
    ClubSettings.objects.filter(fine_base__lte=0).update(fine_base=DEFAULT_FINE_BASE)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_feedback"),
    ]

    operations = [
        migrations.RenameField("clubsettings", "fine_first", "fine_base"),
        migrations.RemoveField("clubsettings", "fine_second"),
        migrations.RemoveField("clubsettings", "fine_third"),
        migrations.AlterField(
            model_name="clubsettings",
            name="fine_base",
            field=models.DecimalField(
                decimal_places=2,
                default=DEFAULT_FINE_BASE,
                max_digits=8,
                validators=[MinValueValidator(Decimal("0.00"))],
                verbose_name="valor da primeira multa",
            ),
        ),
        migrations.RunPython(keep_configured_first_fine, migrations.RunPython.noop),
    ]
