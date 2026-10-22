from django.db.migrations.loader import MigrationLoader


def test_every_app_has_a_single_migration_leaf():
    """Two PRs adding the same migration number merge into two leaves and break `migrate`."""
    conflicts = MigrationLoader(None, ignore_no_migrations=True).detect_conflicts()

    assert conflicts == {}, f"Conflicting migration leaves (makemigrations --merge): {conflicts}"
