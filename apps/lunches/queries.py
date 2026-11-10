from django.db.models import Prefetch, QuerySet

from apps.lunches.models import OrderItem, OrderSession, SwiggyOrder


def with_board(lunches: QuerySet[OrderSession]) -> QuerySet[OrderSession]:
    """Load everything the live board shows in a fixed number of queries."""
    items = OrderItem.objects.select_related("user").order_by(
        "user__name", "created_at"
    )
    order_items = OrderItem.objects.select_related("user").order_by(
        "restaurant_name", "user__name", "created_at"
    )
    orders = SwiggyOrder.objects.order_by("created_at").prefetch_related(
        Prefetch("items", queryset=order_items)
    )
    return lunches.select_related("host").prefetch_related(
        Prefetch("items", queryset=items),
        Prefetch("orders", queryset=orders),
        "skipped_by",
        "completed_by",
    )
