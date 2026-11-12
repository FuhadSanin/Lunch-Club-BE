from django.db import connection
from django.utils import timezone

from apps.core import tenancy
from apps.lunches.models import OrderSession
from tests.conftest import client_for, make_user


def visible_lunches(org_id=None):
    """What a normal (non-superuser) database role sees when scoped to a company."""
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE probe_app")
        # One transaction holds the whole test, so say "no company" explicitly.
        cursor.execute("SELECT set_config('app.org_id', %s, true)", [str(org_id or "")])
        cursor.execute("SELECT org_id FROM order_sessions")
        rows = {str(row[0]) for row in cursor.fetchall()}
        cursor.execute("RESET ROLE")
    return rows


def test_database_shows_only_the_active_companys_lunches(company, other_company):
    for org in (company, other_company):
        OrderSession.objects.create(
            org=org,
            host=make_user(org, f"Host{org.name}"),
            cutoff_at=timezone.now(),
            delivery_address="Somewhere",
        )

    with connection.cursor() as cursor:
        cursor.execute("CREATE ROLE probe_app NOLOGIN")
        cursor.execute("GRANT SELECT ON order_sessions, session_members TO probe_app")

    assert visible_lunches(company.id) == {str(company.id)}
    assert visible_lunches(other_company.id) == {str(other_company.id)}
    assert visible_lunches() == set()


def test_scoped_opens_a_transaction_for_one_company(company):
    with tenancy.scoped(company.id), connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.org_id')")
        assert cursor.fetchone()[0] == str(company.id)


def test_another_company_cannot_read_the_lunch_over_the_api(lunch, other_company):
    outsider = make_user(other_company, "Olga")

    assert client_for(outsider).get(f"/api/lunches/{lunch.id}").status_code == 404
