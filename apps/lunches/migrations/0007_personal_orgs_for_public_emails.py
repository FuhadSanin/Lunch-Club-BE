"""People who share a public email domain (Gmail) stop sharing one org.

Everyone attached to such a shared org gets a personal org ("<first name>'s party") with
every permission. The lunches they host move to it, with their picks and orders (those
follow the lunch). The other people who took part in a moved lunch are added as its
members, so they still see it. The old shared org stays, empty.

Row-level security would hide these rows from a migration, so it is lifted for the two
tables involved while the data moves and put back after.
"""

from django.conf import settings
from django.db import migrations

POLICY_TABLES = ("order_sessions", "session_members")
ALL_PERMISSIONS = [
    "member_create",
    "member_delete",
    "member_read",
    "member_update",
    "company_update",
    "order_update",
    "pick_create",
    "pick_delete",
    "pick_update",
    "restaurant_read",
    "review_create",
    "session_create",
    "session_delete",
    "session_read",
    "session_update",
]


def _has_org(model) -> bool:
    return any(f.name == "org" for f in model._meta.get_fields())


def _first_name(user) -> str:
    return (user.name or user.email.split("@")[0]).split()[0].title()


def give_public_email_people_their_own_party(apps, schema_editor):
    Organization = apps.get_model("accounts", "Organization")
    User = apps.get_model("accounts", "User")
    OrderSession = apps.get_model("lunches", "OrderSession")
    SessionMember = apps.get_model("lunches", "SessionMember")

    shared = Organization.objects.filter(
        kind="company", email_domain__in=settings.PUBLIC_EMAIL_DOMAINS
    )
    people = list(User.objects.filter(org__in=shared).order_by("created_at"))
    if not people:
        return

    cursor = schema_editor.connection.cursor()
    # ALTER TABLE refuses while foreign-key checks are still waiting to run.
    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    for table in POLICY_TABLES:
        cursor.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    for user in people:
        old_org_id = user.org_id
        party = Organization.objects.create(
            name=f"{_first_name(user)}'s party", kind="personal", email_domain=""
        )
        user.org = party
        user.permissions = sorted(ALL_PERMISSIONS)
        user.save(update_fields=["org", "permissions"])
        for lunch in OrderSession.objects.filter(host=user, org_id=old_org_id):
            lunch.org = party
            lunch.save(update_fields=["org"])
            takers = {item.user_id for item in lunch.items.all()}
            takers |= set(lunch.skipped_by.values_list("id", flat=True))
            takers |= set(lunch.completed_by.values_list("id", flat=True))
            for user_id in takers - {user.id}:
                # Later models also record the lunch's org on the row (0008 adds it).
                extra = {"org_id": lunch.org_id} if _has_org(SessionMember) else {}
                SessionMember.objects.get_or_create(
                    session=lunch, user_id=user_id, defaults=extra
                )
    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    for table in POLICY_TABLES:
        cursor.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


class Migration(migrations.Migration):
    dependencies = [("lunches", "0006_party_sessions")]

    operations = [
        migrations.RunPython(
            give_public_email_people_their_own_party, migrations.RunPython.noop
        )
    ]
