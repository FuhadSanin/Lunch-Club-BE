"""Who set each company up.

Companies that already exist get their earliest Host (the person who set them up gets
that role), or, failing that, their earliest person.
"""

import django.db.models.deletion
from django.db import migrations, models

BACKFILL = """
UPDATE organizations o SET created_by_id = (
    SELECT u.id FROM users u
    LEFT JOIN user_roles ur ON ur.user_id = u.id
    LEFT JOIN roles r ON r.id = ur.role_id AND r.name = 'Host' AND r.is_system
    WHERE u.org_id = o.id
    ORDER BY (r.id IS NOT NULL) DESC, u.created_at, u.id
    LIMIT 1
);
"""


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="accounts.user",
            ),
        ),
        migrations.RunSQL(BACKFILL, reverse_sql=migrations.RunSQL.noop),
    ]
