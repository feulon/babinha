from django.db import migrations


class Migration(migrations.Migration):
    """Joins the two 0003 branches (PRs #27 and #29); changes nothing by itself."""

    dependencies = [
        ("finance", "0003_game_charges_open_after_the_game"),
        ("finance", "0003_paymentreminder"),
    ]

    operations = []
