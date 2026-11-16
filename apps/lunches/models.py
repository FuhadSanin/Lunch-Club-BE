from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q

from apps.accounts.models import Address, Organization, User
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
    # What the host calls it ("Friday biryani"); empty shows the host's name instead.
    name = models.CharField(max_length=80, blank=True)
    cutoff_at = models.DateTimeField()
    # Where the food is delivered. Lunches keep it when it leaves the host's list.
    address = models.ForeignKey(
        Address, on_delete=models.PROTECT, related_name="lunches"
    )
    status = models.CharField(
        max_length=12, choices=SessionStatus.choices, default=SessionStatus.OPEN
    )
    # The restaurants the host offered, as shown when it started (id, name, photo,
    # rating...; no menus), so everyone in the lunch sees the same list.
    offered_restaurants = models.JSONField(default=list, blank=True)
    # Whether the link still lets new people in. The host can close it; it also stops
    # at the cutoff, and once picks lock. People already in are not affected.
    joining_open = models.BooleanField(default=True)
    # When the host closed it, after the food arrived and was handed out. A closed
    # lunch is over for everyone in it, in every browser.
    closed_at = models.DateTimeField(null=True, blank=True)
    # The host's ticks while running the lunch, kept here so every device (and a second
    # host) sees them. Restaurants by the name people picked from, people by their id.
    # Reviewing (step 2): this restaurant's dishes are right, this person's order is.
    reviewed_restaurants = ArrayField(
        models.CharField(max_length=200), default=list, blank=True
    )
    reviewed_people = ArrayField(models.UUIDField(), default=list, blank=True)
    # Collecting (step 4): the restaurants whose food has arrived.
    arrived_restaurants = ArrayField(
        models.CharField(max_length=200), default=list, blank=True
    )
    # Handing out (step 5): the people who have their food.
    handed_out_people = ArrayField(models.UUIDField(), default=list, blank=True)
    # People whose picks are final for this lunch: they completed their order or
    # skipped it (completed with no dishes). Only the host can reopen them.
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


class SessionMember(BaseModel):
    """Someone who joined a lunch by its link, from outside the host's org.

    Joining is how people reach a personal party's lunch. The row is what lets the
    database show them that one lunch (see lunches/migrations/0006_party_sessions).
    """

    session = models.ForeignKey(
        OrderSession, on_delete=models.CASCADE, related_name="members"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="joined_sessions"
    )
    # The lunch's org (the host's), so it can see who joined.
    org = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )

    class Meta:
        db_table = "session_members"
        constraints = [
            models.UniqueConstraint(
                fields=["session", "user"], name="session_members_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.session_id}"


class OrderStatus(models.TextChoices):
    PENDING = "pending"  # built at the cutoff, not yet sent to Swiggy
    PLACING = "placing"  # an ordering attempt is in progress
    NEEDS_HOST = "needs_host"  # the host has to finish this order in the Swiggy app
    PLACED = "placed"
    DELIVERED = "delivered"


class Order(BaseModel):
    """One Swiggy checkout; a lunch with more restaurants than one allows has several.

    The row's id doubles as the one-time key that stops the order being placed twice.
    """

    session = models.ForeignKey(
        OrderSession, on_delete=models.CASCADE, related_name="orders"
    )
    platform_order_id = models.CharField(max_length=64, blank=True)
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
        db_table = "orders"

    def __str__(self) -> str:
        return f"Order {self.platform_order_id or self.id} ({self.status})"


class OrderItem(BaseModel):
    """One dish one person picked; linked to its Swiggy order once picks lock."""

    session = models.ForeignKey(
        OrderSession, on_delete=models.CASCADE, related_name="items"
    )
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="order_items")
    order = models.ForeignKey(
        Order,
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
