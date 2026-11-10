"""People whose picks are final for a lunch, with the same company isolation as skips."""

from django.db import migrations, models

ENABLE = """
ALTER TABLE order_session_completions ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_session_completions FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON order_session_completions
    USING (ordersession_id IN (SELECT id FROM order_sessions));
"""

DISABLE = """
DROP POLICY company_isolation ON order_session_completions;
ALTER TABLE order_session_completions NO FORCE ROW LEVEL SECURITY;
ALTER TABLE order_session_completions DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_permissions_instead_of_roles"),
        ("lunches", "0002_row_level_security"),
    ]

    operations = [
        migrations.AddField(
            model_name="ordersession",
            name="completed_by",
            field=models.ManyToManyField(
                blank=True,
                db_table="order_session_completions",
                related_name="completed_lunches",
                to="accounts.user",
            ),
        ),
        migrations.RunSQL(ENABLE, reverse_sql=DISABLE),
    ]
