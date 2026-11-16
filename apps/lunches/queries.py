from django.db.models import Prefetch, QuerySet

from apps.lunches.models import Order, OrderItem, OrderSession


def with_board(lunches: QuerySet[OrderSession]) -> QuerySet[OrderSession]:
    """Load everything the live board shows in a fixed number of queries."""
    items = OrderItem.objects.select_related("user").order_by(
        "user__name", "created_at"
    )
    order_items = OrderItem.objects.select_related("user").order_by(
        "restaurant_name", "user__name", "created_at"
    )
    orders = Order.objects.order_by("created_at").prefetch_related(
        Prefetch("items", queryset=order_items)
    )
    return lunches.select_related("host", "org", "address").prefetch_related(
        Prefetch("items", queryset=items),
        Prefetch("orders", queryset=orders),
        "completed_by",
    )
