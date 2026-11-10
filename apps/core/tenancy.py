"""Company isolation in the database.

Lunch tables carry a PostgreSQL row-level security policy that only shows rows of
the company named in the `app.org_id` setting (see
lunches/migrations/0002_row_level_security). The setting is transaction-local, so it
never leaks to the next request on a pooled connection, and with no company set the
policy shows nothing.
"""

from contextlib import contextmanager
from uuid import UUID

from django.db import connection, transaction


def activate(org_id: UUID) -> None:
    """Scope the current transaction to one company."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.org_id', %s, true)", [str(org_id)])


@contextmanager
def scoped(org_id: UUID):
    """Open a transaction that can only see one company's lunch data."""
    with transaction.atomic():
        activate(org_id)
        yield
