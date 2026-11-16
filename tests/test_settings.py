from config.settings import database_from_url


def test_a_hosted_database_url_becomes_the_database_settings():
    url = "postgresql://app:p%40ss@ep-1-pooler.neon.tech/lunch?sslmode=require"

    database = database_from_url(url)

    assert database["NAME"] == "lunch"
    assert database["USER"] == "app"
    assert database["PASSWORD"] == "p@ss"
    assert database["HOST"] == "ep-1-pooler.neon.tech"
    assert database["PORT"] == "5432"
    assert database["OPTIONS"] == {"sslmode": "require"}
    assert database["CONN_MAX_AGE"] == 0


def test_ssl_is_required_unless_the_url_says_otherwise():
    assert database_from_url("postgresql://a:b@h:6543/d")["OPTIONS"] == {
        "sslmode": "require"
    }
    local = database_from_url("postgresql://a:b@h/d?sslmode=disable")
    assert local["OPTIONS"] == {"sslmode": "disable"}


def test_the_app_starts_in_a_fresh_process_the_way_the_server_starts_it():
    """A circular import between the settings, sign-in and the error handler only shows
    when modules load in the server's order, which the test run's order hides."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    result = subprocess.run(  # noqa: S603  our own interpreter, a fixed command
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=Path(__file__).resolve().parent.parent,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.test_settings"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-1500:]
