import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from apps.accounts.models import Organization, User
from apps.lunches.models import OrderSession

# Dropping tables can't run inside the transaction the other tests use, so these commit.
pytestmark = pytest.mark.django_db(transaction=True)


def tables():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        )
        return {row[0] for row in cursor.fetchall()}


def test_without_yes_it_lists_the_tables_and_drops_nothing(lunch, settings, capsys):
    settings.DEBUG = True

    with pytest.raises(CommandError, match="--yes"):
        call_command("reset_database")

    assert "order_sessions" in capsys.readouterr().out
    assert OrderSession.objects.count() == 1


def test_a_deployed_database_needs_an_extra_flag(lunch, settings):
    settings.DEBUG = False

    with pytest.raises(CommandError, match="--allow-production"):
        call_command("reset_database", "--yes")

    assert OrderSession.objects.count() == 1


def test_yes_drops_everything_and_builds_todays_schema(lunch, settings):
    settings.DEBUG = True
    with connection.cursor() as cursor:  # a leftover from an older version of the app
        cursor.execute("CREATE TABLE swiggy_orders_old (id int)")
    before = tables() - {"swiggy_orders_old"}

    call_command("reset_database", "--yes", verbosity=0)

    assert tables() == before  # the same tables as a fresh database, no leftover
    assert OrderSession.objects.count() == 0
    assert User.objects.count() == 0
    assert Organization.objects.count() == 0
    # the schema is usable, with row-level security back in place
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_policies WHERE schemaname = current_schema()"
        )
        assert cursor.fetchone()[0] > 0
