from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.core.rls import roles_that_bypass_rls


class Command(BaseCommand):
    help = (
        "Say whether the database role the app connects as can skip row-level security "
        "(it must not: that wall keeps companies' lunches apart). Exits 1 if it can."
    )

    def handle(self, *args, **options):
        found = roles_that_bypass_rls()
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            user = cursor.fetchone()[0]
        if found:
            raise CommandError(
                f"'{user}' can skip row-level security (through: {', '.join(found)}). "
                "Run `manage.py create_app_role` and connect as that role."
            )
        self.stdout.write(
            self.style.SUCCESS(f"'{user}' is subject to row-level security.")
        )
