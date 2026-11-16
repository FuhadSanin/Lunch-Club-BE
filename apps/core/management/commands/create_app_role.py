import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = (
        "Make (or reset the password of) a plain database role for the app to connect "
        "as: no superuser, no BYPASSRLS, only the rights to read and write the tables. "
        "Run it as the database's owner (the role you migrate with); then point "
        "DATABASE_URL at the new role. Migrations keep running as the owner."
    )

    def add_arguments(self, parser):
        parser.add_argument("--name", default="lunch_app", help="the role's name")
        parser.add_argument(
            "--password", help="its password (default: a long random one, printed once)"
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("Row-level security is a PostgreSQL feature.")
        name = options["name"]
        if not name.replace("_", "").isalnum():
            raise CommandError(
                "Use letters, digits and underscores in the role's name."
            )
        password = options["password"] or secrets.token_urlsafe(32)
        role = connection.ops.quote_name(name)
        database = connection.ops.quote_name(connection.settings_dict["NAME"])
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [name])
            verb = "ALTER" if cursor.fetchone() else "CREATE"
            cursor.execute(
                f"{verb} ROLE {role} LOGIN PASSWORD %s NOSUPERUSER NOBYPASSRLS "
                "NOCREATEDB NOCREATEROLE",
                [password],
            )
            for statement in (
                f"GRANT CONNECT ON DATABASE {database} TO {role}",
                f"GRANT USAGE ON SCHEMA public TO {role}",
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                f"TO {role}",
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
                # tables the owner makes later (new migrations) are reachable too
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, "
                f"UPDATE, DELETE ON TABLES TO {role}",
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON "
                f"SEQUENCES TO {role}",
            ):
                cursor.execute(statement)
        db = connection.settings_dict
        self.stdout.write(
            self.style.SUCCESS(f"Role '{name}' is ready (no superuser, no BYPASSRLS).")
        )
        if not options["password"]:
            host = db["HOST"] or "localhost"
            self.stdout.write(
                f"\nIts password is shown only now:\n  {password}\n\n"
                "Use it in the app's DATABASE_URL (keep the owner's URL for "
                "migrations):\n"
                f"  postgresql://{name}:{password}@{host}:{db['PORT'] or 5432}/"
                f"{db['NAME']}?sslmode=require\n"
                "For Neon use the pooled host (ep-...-pooler...) in the app's URL."
            )
