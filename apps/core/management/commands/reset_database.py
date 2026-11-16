from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction


class Command(BaseCommand):
    help = (
        "Start the database over: drop every table (all data, and the record of "
        "which migrations ran), then build today's schema from the migrations. For a "
        "database whose old data isn't needed, such as one made before the migrations "
        "were squashed. Run it as the database's owner. Needs --yes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes", action="store_true", help="Really drop everything."
        )
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Also allow it when DJANGO_DEBUG is off (a deployed database).",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("This is for the PostgreSQL database the app uses.")
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError(
                "DJANGO_DEBUG is off, so this looks like a deployed database. "
                "Run it with --allow-production if you really mean to start it over."
            )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() "
                "ORDER BY tablename"
            )
            tables = [row[0] for row in cursor.fetchall()]
        db = connection.settings_dict
        where = f"{db['NAME']} on {db['HOST'] or 'localhost'}"
        if not options["yes"]:
            self.stdout.write(
                f"This would drop {len(tables)} tables in {where}, with all their data:"
            )
            self.stdout.write("  " + (", ".join(tables) or "(none)"))
            raise CommandError("Nothing dropped. Run again with --yes to do it.")

        # All or nothing: a failure part-way leaves the database as it was.
        with transaction.atomic(), connection.cursor() as cursor:
            for table in tables:
                cursor.execute(
                    f"DROP TABLE IF EXISTS {connection.ops.quote_name(table)} CASCADE"
                )
        self.stdout.write(
            f"Dropped {len(tables)} tables in {where}. Building it again:"
        )
        call_command("migrate", interactive=False, verbosity=options["verbosity"])
        self.stdout.write(
            self.style.SUCCESS(f"{where} is empty and on today's schema.")
        )
