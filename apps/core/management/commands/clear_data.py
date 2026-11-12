from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connection, transaction


class Command(BaseCommand):
    help = (
        "Delete every row in every app table (companies, people, lunches, picks, "
        "orders) and reset the ids. Tables and migrations stay. Needs --yes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes", action="store_true", help="Really delete everything."
        )
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Also allow it when DJANGO_DEBUG is off (a deployed database).",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError(
                "DJANGO_DEBUG is off, so this looks like a deployed database. "
                "Run it with --allow-production if you really mean to empty it."
            )
        tables = sorted(
            {
                model._meta.db_table
                for model in apps.get_models(include_auto_created=True)
                if model._meta.managed and not model._meta.proxy
            }
        )
        db = connection.settings_dict
        where = f"{db['NAME']} on {db['HOST'] or 'localhost'}"
        if not options["yes"]:
            self.stdout.write(
                f"This would delete all rows from {len(tables)} tables in {where}:"
            )
            self.stdout.write("  " + ", ".join(tables))
            raise CommandError("Nothing deleted. Run again with --yes to do it.")

        # TRUNCATE skips row-level security; CASCADE covers links between tables.
        statements = connection.ops.sql_flush(
            no_style(), tables, reset_sequences=True, allow_cascade=True
        )
        with transaction.atomic():
            for sql in statements:
                connection.cursor().execute(sql)
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted all data from {len(tables)} tables in {where}."
            )
        )
