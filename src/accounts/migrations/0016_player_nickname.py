import django.db.models.functions.text
from django.db import migrations, models

NICKNAME_MAX_LENGTH = 30


def copy_name_to_nickname(apps, schema_editor):
    """Nobody is left without a nickname: the one name they had is also their nickname."""
    Player = apps.get_model("accounts", "Player")
    for player in Player.objects.all():
        player.nickname = player.name.strip()[:NICKNAME_MAX_LENGTH]
        player.save(update_fields=["nickname"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0015_tipos_primeiro_login"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="nickname",
            field=models.CharField(default="", max_length=30, verbose_name="apelido"),
            preserve_default=False,
        ),
        migrations.RunPython(copy_name_to_nickname, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="player",
            name="name",
            field=models.CharField(max_length=60, verbose_name="nome"),
        ),
        migrations.AlterField(
            model_name="player",
            name="nickname",
            field=models.CharField(max_length=30, verbose_name="apelido"),
        ),
        migrations.AlterModelOptions(
            name="player",
            options={
                "ordering": [
                    django.db.models.functions.text.Lower("nickname"),
                    django.db.models.functions.text.Lower("name"),
                ],
                "verbose_name": "jogador",
                "verbose_name_plural": "jogadores",
            },
        ),
    ]
