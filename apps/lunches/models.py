from django.db import models
from django.db.models import Q

from apps.accounts.models import Organization, User
from apps.core.models import BaseModel


class SessionStatus(models.TextChoices):
    OPEN = "open"  # people are picking
    LOCKED = "locked"  # cutoff passed, cart built, waiting for the host to confirm
    CONFIRMED = "confirmed"  # host confirmed, orders are being placed
    PLACED = "placed"  # every Swiggy order is placed
    DELIVERED = "delivered"  # every Swiggy order has arrived
    CANCELLED = "cancelled"


class OrderSession(BaseModel):
    """One group lunch: a host, a cutoff time and everyone's picks."""

    org = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="lunches"
    )
    host = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="hosted_lunches"
    )
    cutoff_at = models.DateTimeField()
    delivery_address = models.TextField()
    status = models.CharField(
        max_length=12, choices=SessionStatus.choices, default=SessionStatus.OPEN
    )
    skipped_by = models.ManyToManyField(
        User, related_name="skipped_lunches", db_table="order_session_skips", blank=True
    )
    # People whose picks are final for this lunch: they completed their order or
    # skipped. Only the host can reopen them.
    completed_by = models.ManyToManyField(
        User,
        related_name="completed_lunches",
        db_table="order_session_completions",
        blank=True,
    )

    class Meta:
        db_table = "order_sessions"
        indexes = [
            models.Index(
                fields=["org", "-created_at"], name="order_sessions_org_recent"
            )
        ]

    def __str__(self) -> str:
        return f"Lunch closing {self.cutoff_at:%Y-%m-%d %H:%M} ({self.status})"


class OrderStatus(models.TextChoices):
    PENDING = "pending"  # built at the cutoff, not yet sent to Swiggy
    PLACING = "placing"  # an ordering attempt is in progress
    NEEDS_HOST = "needs_host"  # the host has to finish this order in the Swiggy app
    PLACED = "placed"
    DELIVERED = "delivered"


class SwiggyOrder(BaseModel):
    """One Swiggy checkout; a lunch with more restaurants than one allows has several.

    The row's id doubles as the one-time key that stops the order being placed twice.
    """

    session = models.ForeignKey(
        OrderSession, on_delete=models.CASCADE, related_name="orders"
    )
    swiggy_order_id = models.CharField(max_length=64, blank=True)
    restaurant_count = models.PositiveSmallIntegerField()
    total_paise = models.PositiveIntegerField()
    status = models.CharField(
        max_length=12, choices=OrderStatus.choices, default=OrderStatus.PENDING
    )
    placed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    # The order bot's checkpoints for its latest attempt (see apps.ordering.progress).
    bot_steps = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "swiggy_orders"

    def __str__(self) -> str:
        return f"Swiggy order {self.swiggy_order_id or self.id} ({self.status})"


class OrderItem(BaseModel):
    """One dish one person picked; linked to its Swiggy order once picks lock."""

    session = models.ForeignKey(
        OrderSession, on_delete=models.CASCADE, related_name="items"
    )
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="order_items")
    order = models.ForeignKey(
        SwiggyOrder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="items",
    )
    restaurant_name = models.CharField(max_length=200)
    # Swiggy's id for the restaurant, so the host can open its page; empty if unknown.
    restaurant_id = models.CharField(max_length=20, blank=True)
    dish_name = models.CharField(max_length=200)
    quantity = models.PositiveSmallIntegerField()
    price_paise = models.PositiveIntegerField()
    note = models.TextField(blank=True)

    class Meta:
        db_table = "order_items"
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gte=1, quantity__lte=20),
                name="order_items_quantity_range",
            )
        ]

    def __str__(self) -> str:
        return f"{self.quantity} × {self.dish_name} from {self.restaurant_name}"

    @property
    def total_paise(self) -> int:
        return self.quantity * self.price_paise
