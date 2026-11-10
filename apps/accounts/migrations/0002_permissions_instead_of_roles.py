"""People get permissions instead of a role.

Each role becomes the permissions it used to allow, so nobody gains or loses access.
The lists are written out here rather than imported, so this migration keeps doing
what it did when it was written after the catalogue in models.py changes.
"""

import django.contrib.postgres.fields
from django.db import migrations, models

MEMBER = [
    "member_read",
    "pick_create",
    "pick_delete",
    "pick_update",
    "restaurant_read",
    "session_read",
]
HOST = sorted(
    [
        *MEMBER,
        "order_update",
        "review_create",
        "session_create",
        "session_delete",
        "session_update",
    ]
)
ADMIN = sorted([*HOST, "member_update"])
PERMISSIONS_BY_ROLE = {"admin": ADMIN, "host": HOST, "member": MEMBER}

CATALOGUE = [
    ("session_create", "Start a lunch"),
    ("session_read", "See lunches and their live boards"),
    ("session_update", "Lock a lunch's picks before the cutoff"),
    ("session_delete", "Cancel a lunch that isn't ordered yet"),
    ("pick_create", "Pick dishes for a lunch"),
    ("pick_update", "Change their picks, or skip a lunch"),
    ("pick_delete", "Remove their picks"),
    ("review_create", "Confirm a locked lunch's cart, so its orders go out"),
    ("order_update", "Enter Swiggy order numbers and mark orders delivered"),
    ("restaurant_read", "Browse Swiggy restaurants, menus and dishes"),
    ("member_read", "See everyone in the company and their permissions"),
    ("member_update", "Grant and remove people's permissions"),
]


def roles_to_permissions(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    # People without a company keep no permissions, whatever their old role said.
    for role, permissions in PERMISSIONS_BY_ROLE.items():
        User.objects.filter(role=role, org__isnull=False).update(
            permissions=permissions
        )


def permissions_to_roles(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(permissions__contains=["session_create"]).update(role="host")
    User.objects.filter(permissions__contains=["member_update"]).update(role="admin")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="permissions",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(choices=CATALOGUE, max_length=40),
                blank=True,
                default=list,
            ),
        ),
        migrations.RunPython(roles_to_permissions, permissions_to_roles),
        migrations.RemoveField(
            model_name="user",
            name="role",
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("permissions__contained_by", [code for code, _ in CATALOGUE])
                ),
                name="users_permissions_known",
            ),
        ),
    ]
