"""Row-level security: each company sees only its own lunches, even if a query forgets to filter.

The policies read the transaction-local `app.org_id` setting (see apps.core.tenancy).
FORCE applies them to the table owner too, which is the role the app connects as.
Superusers bypass row-level security, so the app's database role must not be one.
"""

from django.db import migrations

CURRENT_ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"

ENABLE = f"""
ALTER TABLE order_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON order_sessions
    USING (org_id = {CURRENT_ORG});

ALTER TABLE order_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_items FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON order_items
    USING (session_id IN (SELECT id FROM order_sessions));

ALTER TABLE swiggy_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE swiggy_orders FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON swiggy_orders
    USING (session_id IN (SELECT id FROM order_sessions));

ALTER TABLE order_session_skips ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_session_skips FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON order_session_skips
    USING (ordersession_id IN (SELECT id FROM order_sessions));
"""

DISABLE = """
DROP POLICY company_isolation ON order_session_skips;
ALTER TABLE order_session_skips NO FORCE ROW LEVEL SECURITY;
ALTER TABLE order_session_skips DISABLE ROW LEVEL SECURITY;
DROP POLICY company_isolation ON swiggy_orders;
ALTER TABLE swiggy_orders NO FORCE ROW LEVEL SECURITY;
ALTER TABLE swiggy_orders DISABLE ROW LEVEL SECURITY;
DROP POLICY company_isolation ON order_items;
ALTER TABLE order_items NO FORCE ROW LEVEL SECURITY;
ALTER TABLE order_items DISABLE ROW LEVEL SECURITY;
DROP POLICY company_isolation ON order_sessions;
ALTER TABLE order_sessions NO FORCE ROW LEVEL SECURITY;
ALTER TABLE order_sessions DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("lunches", "0001_initial")]

    operations = [migrations.RunSQL(ENABLE, reverse_sql=DISABLE)]
