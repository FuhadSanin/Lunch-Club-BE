"""The database role the app connects as must not be able to skip row-level security."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from apps.core import rls

pytestmark = pytest.mark.django_db


def as_role(name):
    """Become `name` for the rest of the test's transaction (it rolls back after)."""
    with connection.cursor() as cursor:
        cursor.execute(f'SET LOCAL ROLE "{name}"')


def test_the_test_database_user_is_a_superuser_so_it_is_flagged():
    assert rls.roles_that_bypass_rls() != []


def test_check_rls_fails_for_a_role_that_can_skip_it():
    with pytest.raises(CommandError, match="can skip row-level security"):
        call_command("check_rls")


def test_create_app_role_makes_a_role_that_cannot_skip_it(capsys):
    call_command("create_app_role", "--name", "probe_app")

    out = capsys.readouterr().out
    assert "Role 'probe_app' is ready" in out
    assert "postgresql://probe_app:" in out and "sslmode=require" in out
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles "
            "WHERE rolname = 'probe_app'"
        )
        assert cursor.fetchone() == (False, False, True)
    as_role("probe_app")
    assert rls.roles_that_bypass_rls() == []
    call_command("check_rls")  # passes now: no error


def test_the_new_role_can_use_the_tables_but_not_change_them(company):
    call_command("create_app_role", "--name", "probe_app", "--password", "pw-for-test")
    as_role("probe_app")

    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM organizations")  # reading is allowed
        with pytest.raises(Exception, match="permission denied"):
            cursor.execute("CREATE TABLE sneaky (id int)")
        # (a failed statement ends the transaction; nothing more to run here)


def test_running_it_again_resets_the_password_and_keeps_the_role(capsys):
    call_command("create_app_role", "--name", "probe_app", "--password", "first")
    call_command("create_app_role", "--name", "probe_app", "--password", "second")

    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'probe_app'")
        assert cursor.fetchone()[0] == 1
    assert "shown only now" not in capsys.readouterr().out  # the password was given


def test_a_bad_role_name_is_refused():
    with pytest.raises(CommandError, match="letters, digits"):
        call_command("create_app_role", "--name", 'x"; DROP TABLE users; --')


def test_require_rls_stops_the_app_when_the_role_can_skip_it(settings):
    settings.REQUIRE_RLS = True

    with pytest.raises(ImproperlyConfigured, match="REQUIRE_RLS is on"):
        rls.require_rls_on_connect(None, connection)


def test_require_rls_lets_a_plain_role_through(settings):
    settings.REQUIRE_RLS = True
    call_command("create_app_role", "--name", "probe_app")
    as_role("probe_app")

    rls.require_rls_on_connect(None, connection)  # no error


def test_without_require_rls_nothing_is_checked(settings):
    settings.REQUIRE_RLS = False

    rls.require_rls_on_connect(None, connection)  # superuser, but not required


def test_wildcard_allowed_hosts_are_flagged_in_production(settings):
    from apps.core import checks

    settings.DEBUG = False
    settings.ALLOWED_HOSTS = [".vercel.app", "tiffmate-api.vercel.app", "*"]

    found = checks.no_wildcard_hosts(None)

    assert [w.id for w in found] == ["lunch.W001"]
    assert ".vercel.app" in found[0].msg and "*" in found[0].msg


def test_exact_hosts_and_local_development_are_fine(settings):
    from apps.core import checks

    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ["tiffmate-api.vercel.app", "localhost"]
    assert checks.no_wildcard_hosts(None) == []

    settings.DEBUG = True
    settings.ALLOWED_HOSTS = ["*"]
    assert checks.no_wildcard_hosts(None) == []
