"""Everyone in a party sees who else is in it.

A membership row now carries the org of the lunch it is for (the host's), so that org
can see who joined its lunches. The policy still reads only settings, never another
table, so it cannot recurse into order_sessions' policy:

    own_membership: user_id = app.user_id OR org_id = app.org_id

People who joined see the others through the board, which the server builds after
checking they take part (see LunchDetailSerializer.people). Existing rows are filled in
with row-level security lifted for the two tables while the data moves, then put back.
"""

import django.db.models.deletion
from django.db import migrations, models

CURRENT_ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"
CURRENT_USER = "NULLIF(current_setting('app.user_id', true), '')::uuid"

BACKFILL = """
ALTER TABLE session_members NO FORCE ROW LEVEL SECURITY;
ALTER TABLE order_sessions NO FORCE ROW LEVEL SECURITY;
UPDATE session_members m SET org_id = s.org_id FROM order_sessions s WHERE s.id = m.session_id;
ALTER TABLE order_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE session_members FORCE ROW LEVEL SECURITY;
"""

POLICY = f"""
DROP POLICY own_membership ON session_members;
CREATE POLICY own_membership ON session_members
    USING (user_id = {CURRENT_USER} OR org_id = {CURRENT_ORG});
"""

OLD_POLICY = f"""
DROP POLICY own_membership ON session_members;
CREATE POLICY own_membership ON session_members
    USING (user_id = {CURRENT_USER});
"""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_organization_kind"),
        ("lunches", "0007_personal_orgs_for_public_emails"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionmember",
            name="org",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="accounts.organization",
            ),
        ),
        migrations.RunSQL(BACKFILL, migrations.RunSQL.noop),
        migrations.AlterField(
            model_name="sessionmember",
            name="org",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="accounts.organization",
            ),
        ),
        migrations.RunSQL(POLICY, OLD_POLICY),
    ]
