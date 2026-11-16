from django.core.management import call_command
from django.db import migrations

TABLE = "django_throttle_cache"


def create(apps, schema_editor):
    """The table the shared rate-limit cache uses (THROTTLE_CACHE=database). Made always,
    so turning that on later needs no extra step."""
    call_command("createcachetable", TABLE, database=schema_editor.connection.alias)


def drop(apps, schema_editor):
    schema_editor.execute(f'DROP TABLE IF EXISTS "{TABLE}"')


class Migration(migrations.Migration):
    dependencies: list = []

    operations = [migrations.RunPython(create, drop)]
