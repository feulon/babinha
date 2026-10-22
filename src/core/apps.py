from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Núcleo"

    def ready(self) -> None:
        from core import fake_clock

        fake_clock.install_from_settings()
