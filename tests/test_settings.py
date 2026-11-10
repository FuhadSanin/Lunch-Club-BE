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
