"""Company isolation in the database.

Lunch tables carry a PostgreSQL row-level security policy that only shows rows of
the company named in the `app.org_id` setting (see
lunches/migrations/0002_row_level_security), plus the lunches the person named in
`app.user_id` joined (lunches/migrations/0006_party_sessions). The settings are
transaction-local, so they never leak to the next request on a pooled connection, and
with nothing set the policies show nothing.
"""

from contextlib import contextmanager
from uuid import UUID

from django.db import connection, transaction


def activate(org_id: UUID | None, user_id: UUID | None = None) -> None:
    """Scope the current transaction to one company, and to the lunches one person
    joined. A person with no company passes None and sees only what they joined."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.org_id', %s, true),"
            " set_config('app.user_id', %s, true)",
            [str(org_id or ""), str(user_id or "")],
        )


@contextmanager
def joining(lunch_id: UUID):
    """Let the transaction read exactly one lunch outside the caller's scope.

    This is how a shared link works: only the server calls it, with the id from the
    link, and the lunch is readable (not writable) only inside this block. The id is an
    unguessable UUID, so knowing it is the capability. The caller must add the
    membership inside the block; after it the lunch is visible by membership alone.
    """
    _set("app.join_session", str(lunch_id))
    try:
        yield
    finally:
        _set("app.join_session", "")


@contextmanager
def reading_org(org_id: UUID):
    """Let the transaction read one org's rows for a moment, then go back.

    Only for the server to read something a person is already allowed to know about a
    lunch they take part in (who else is in it), never with an id a client chose.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.org_id', true)")
        before = cursor.fetchone()[0] or ""
    _set("app.org_id", str(org_id))
    try:
        yield
    finally:
        _set("app.org_id", before)


def _set(name: str, value: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [name, value])


@contextmanager
def scoped(org_id: UUID):
    """Open a transaction that can only see one company's lunch data."""
    with transaction.atomic():
        activate(org_id)
        yield
