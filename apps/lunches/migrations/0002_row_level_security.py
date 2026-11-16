"""Row-level security: each company sees only its own lunches, even if a query forgets
to filter.

The policies read three transaction-local settings (see apps.core.tenancy):

- `app.org_id`: the company the request is for. A lunch is visible to its company, and
  to anyone who joined it (a `session_members` row, for personal parties).
- `app.user_id`: the person, whose own memberships are visible; the lunch's company also
  sees who joined its lunches. session_members' policy reads only settings, never
  another table, so it can't recurse into order_sessions' policy.
- `app.join_session`: the one lunch being opened from a shared link, readable (not
  writable) for that transaction only.

Picks, orders and completions follow the lunches they belong to. FORCE applies the
policies to the table owner too, which is the role the app connects as. Superusers bypass
row-level security, so the app's database role must not be one.
"""

from django.db import migrations

CURRENT_ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"
CURRENT_USER = "NULLIF(current_setting('app.user_id', true), '')::uuid"
JOINING = "NULLIF(current_setting('app.join_session', true), '')::uuid"

FOLLOWS_LUNCH = {
    "order_items": "session_id",
    "orders": "session_id",
    "order_session_completions": "ordersession_id",
}
TABLES = ["order_sessions", "session_members", *FOLLOWS_LUNCH]

ENABLE = (
    "".join(
        f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
"""
        for table in TABLES
    )
    + f"""
CREATE POLICY own_membership ON session_members
    USING (user_id = {CURRENT_USER} OR org_id = {CURRENT_ORG});

CREATE POLICY company_isolation ON order_sessions
    USING (
        org_id = {CURRENT_ORG}
        OR id IN (SELECT session_id FROM session_members)
    );
CREATE POLICY join_by_link ON order_sessions FOR SELECT
    USING (id = {JOINING});
"""
    + "".join(
        f"""
CREATE POLICY company_isolation ON {table}
    USING ({column} IN (SELECT id FROM order_sessions));
"""
        for table, column in FOLLOWS_LUNCH.items()
    )
)

DISABLE = (
    "DROP POLICY join_by_link ON order_sessions;\n"
    "DROP POLICY company_isolation ON order_sessions;\n"
    "DROP POLICY own_membership ON session_members;\n"
    + "".join(
        f"DROP POLICY company_isolation ON {table};\n" for table in FOLLOWS_LUNCH
    )
    + "".join(
        f"""ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""
        for table in TABLES
    )
)


class Migration(migrations.Migration):
    dependencies = [("lunches", "0001_initial")]

    operations = [migrations.RunSQL(ENABLE, reverse_sql=DISABLE)]
