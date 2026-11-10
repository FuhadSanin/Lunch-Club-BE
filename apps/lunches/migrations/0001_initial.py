import uuid

import django.db.models.deletion
from django.db import migrations, models


def _base_fields():
    return [
        (
            "id",
            models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
        ),
        ("created_at", models.DateTimeField(auto_now_add=True)),
        ("updated_at", models.DateTimeField(auto_now=True)),
    ]


class Migration(migrations.Migration):
    initial = True

    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="OrderSession",
            fields=[
                *_base_fields(),
                ("cutoff_at", models.DateTimeField()),
                ("delivery_address", models.TextField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("locked", "Locked"),
                            ("confirmed", "Confirmed"),
                            ("placed", "Placed"),
                            ("delivered", "Delivered"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="open",
                        max_length=12,
                    ),
                ),
                (
                    "host",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hosted_lunches",
                        to="accounts.user",
                    ),
                ),
                (
                    "org",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="lunches",
                        to="accounts.organization",
                    ),
                ),
                (
                    "skipped_by",
                    models.ManyToManyField(
                        blank=True,
                        db_table="order_session_skips",
                        related_name="skipped_lunches",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "db_table": "order_sessions",
                "indexes": [
                    models.Index(fields=["org", "-created_at"], name="order_sessions_org_recent")
                ],
            },
        ),
        migrations.CreateModel(
            name="SwiggyOrder",
            fields=[
                *_base_fields(),
                ("swiggy_order_id", models.CharField(blank=True, max_length=64)),
                ("restaurant_count", models.PositiveSmallIntegerField()),
                ("total_paise", models.PositiveIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("placing", "Placing"),
                            ("needs_host", "Needs Host"),
                            ("placed", "Placed"),
                            ("delivered", "Delivered"),
                        ],
                        default="pending",
                        max_length=12,
                    ),
                ),
                ("placed_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orders",
                        to="lunches.ordersession",
                    ),
                ),
            ],
            options={"db_table": "swiggy_orders"},
        ),
        migrations.CreateModel(
            name="OrderItem",
            fields=[
                *_base_fields(),
                ("restaurant_name", models.CharField(max_length=200)),
                ("dish_name", models.CharField(max_length=200)),
                ("quantity", models.PositiveSmallIntegerField()),
                ("price_paise", models.PositiveIntegerField()),
                ("note", models.TextField(blank=True)),
                (
                    "order",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="items",
                        to="lunches.swiggyorder",
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="lunches.ordersession",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="order_items",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "db_table": "order_items",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("quantity__gte", 1), ("quantity__lte", 20)),
                        name="order_items_quantity_range",
                    )
                ],
            },
        ),
    ]
