"""Checking that the database role the app connects as can't skip row-level security.

Lunch tables only show a company its own rows because of PostgreSQL row-level security
(see core/tenancy.py). A superuser, or a role with BYPASSRLS, skips it, and so does any
role that inherits one: Neon's default `neondb_owner` is in `neon_superuser`, which
does.
The queries also filter by company, so this is a second wall, not the only one, but the
app should still connect as a plain role: `manage.py create_app_role` makes one, and
REQUIRE_RLS=true makes the app refuse to run as anything else.
"""

import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.backends.signals import connection_created

logger = logging.getLogger(__name__)

# Any role the current one is, or inherits, that is a superuser or has BYPASSRLS.
_BYPASSING_ROLES = """
SELECT r.rolname
FROM pg_roles r
WHERE pg_has_role(current_user, r.oid, 'USAGE') AND (r.rolsuper OR r.rolbypassrls)
ORDER BY r.rolname
"""


def roles_that_bypass_rls(conn=connection) -> list[str]:
    """Which roles give the connection's user the power to skip row-level security
    (empty when it can't). Only PostgreSQL has any."""
    if conn.vendor != "postgresql":
        return []
    with conn.cursor() as cursor:
        cursor.execute(_BYPASSING_ROLES)
        return [row[0] for row in cursor.fetchall()]


def require_rls_on_connect(sender, connection, **kwargs):
    """With REQUIRE_RLS on, stop at once, on the first connection, if the app's role can
    skip row-level security, instead of quietly running without that wall."""
    if not settings.REQUIRE_RLS or connection.vendor != "postgresql":
        return
    found = roles_that_bypass_rls(connection)
    if found:
        raise ImproperlyConfigured(
            "REQUIRE_RLS is on, but the database role the app connects as can skip "
            f"row-level security (through: {', '.join(found)}). Run "
            "`manage.py create_app_role` and connect as that role."
        )


def install() -> None:
    connection_created.connect(
        require_rls_on_connect, dispatch_uid="apps.core.rls.require"
    )
