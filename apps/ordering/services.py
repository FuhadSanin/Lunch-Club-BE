"""Turning a confirmed lunch into Swiggy orders, each placed exactly once.

Order of row locks, always: the lunch, then its Swiggy order. Locking the lunch
first means two orders of one lunch finishing together can't both miss the "all
placed" moment.
"""

import logging
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from apps.core import jobs, notify, tenancy
from apps.core.errors import Conflict, RetryLater
from apps.lunches import board
from apps.lunches.models import OrderSession, OrderStatus, SessionStatus, SwiggyOrder
from apps.lunches.services import lunch_link
from apps.ordering.progress import Progress
from apps.ordering.providers import get_provider
from apps.ordering.providers.base import CartLine, HostActionRequired, PlaceRequest

logger = logging.getLogger(__name__)

# How long delivery is tracked before the host is left to mark it: 36 polls of 5
# minutes.
MAX_DELIVERY_POLLS = 36


def lock_order(order_id: UUID) -> SwiggyOrder | None:
    """Lock a Swiggy order and its lunch, in the documented order."""
    lunch_id = (
        SwiggyOrder.objects.filter(id=order_id)
        .values_list("session_id", flat=True)
        .first()
    )
    if lunch_id is None:
        return None
    lunch = (
        OrderSession.objects.select_for_update(of=("self",))
        .select_related("host")
        .get(id=lunch_id)
    )
    order = SwiggyOrder.objects.select_for_update().get(id=order_id)
    order.session = lunch
    return order


def confirm(lunch: OrderSession) -> None:
    """The host approves the cart; each Swiggy order is queued to be placed.

    Expects `lunch` locked with select_for_update.
    """
    if lunch.status != SessionStatus.LOCKED:
        raise Conflict("Only a lunch whose picks are locked can be confirmed.")
    lunch.status = SessionStatus.CONFIRMED
    lunch.save(update_fields=["status", "updated_at"])
    for order in lunch.orders.all():
        payload = {"org_id": str(lunch.org_id), "order_id": str(order.id)}
        jobs.enqueue("place-order", payload, key=str(order.id))
    board.publish(lunch)


def place(org_id: UUID, order_id: UUID) -> None:
    """Place one Swiggy order. Runs as the place-order job and is safe to run again."""
    with tenancy.scoped(org_id):
        order = lock_order(order_id)
        if order is None or order.status not in (
            OrderStatus.PENDING,
            OrderStatus.PLACING,
        ):
            return
        if order.status == OrderStatus.PENDING and _waits_for_earlier_order(order):
            # Restaurant N is only filled after restaurant N-1 is ordered; it is
            # started again when the host marks that one as ordered.
            order.last_error = "Waiting for the previous restaurant to be ordered."
            order.save(update_fields=["last_error", "updated_at"])
            board.publish(order.session)
            return
        interrupted = order.status == OrderStatus.PLACING
        lease = timedelta(seconds=settings.PLACEMENT_LEASE_SECONDS)
        if interrupted and timezone.now() - order.updated_at < lease:
            raise RetryLater("An attempt to place this order is still running.")
        # Committed before the slow checkout starts, so a crash mid-checkout is visible
        # to the retry, which then checks Swiggy before ordering again.
        order.status = OrderStatus.PLACING
        order.bot_steps = []  # a fresh checklist for this attempt
        order.save(update_fields=["status", "bot_steps", "updated_at"])
        request = _place_request(order)

    provider = get_provider()
    progress = Progress(lambda steps: _save_steps(org_id, order_id, steps))
    try:
        result = _call(provider.find_existing, request) if interrupted else None
        result = result or _call(provider.place, request, progress)
    except HostActionRequired as reason:
        _hand_to_host(org_id, order_id, str(reason))
        return
    except Exception:
        logger.exception(
            "Placing a Swiggy order failed", extra={"fields": {"order_id": order_id}}
        )
        _hand_to_host(
            org_id,
            order_id,
            "Automatic ordering failed. Check the Swiggy app first in case the order "
            "went through, then place it there and enter its Swiggy order number here.",
        )
        return

    with tenancy.scoped(org_id):
        order = lock_order(order_id)
        if order is not None and order.status == OrderStatus.PLACING:
            _set_placed(order, result.swiggy_order_id)


def retry(order: SwiggyOrder) -> None:
    """The host asks the bot to try this order again, e.g. after logging in again.

    Expects `order` from lock_order.
    """
    if order.status != OrderStatus.NEEDS_HOST:
        raise Conflict("Only an order that is waiting for you can be retried.")
    order.status = OrderStatus.PENDING
    order.last_error = ""
    order.save(update_fields=["status", "last_error", "updated_at"])
    payload = {"org_id": str(order.session.org_id), "order_id": str(order.id)}
    # A fresh key per retry: the job's own key is spent on the first attempt.
    jobs.enqueue("place-order", payload, key=f"{order.id}-retry-{time.time_ns()}")
    board.publish(order.session)


def record_placed(order: SwiggyOrder, swiggy_order_id: str) -> None:
    """The host placed this order in the Swiggy app. Expects `order` from lock_order."""
    if order.status != OrderStatus.NEEDS_HOST:
        raise Conflict("This order isn't waiting for you.")
    _set_placed(order, swiggy_order_id)
    _start_next_order(order)


def record_delivered(order: SwiggyOrder) -> None:
    """The host has the food. Expects `order` from lock_order."""
    if order.status != OrderStatus.PLACED:
        raise Conflict("Only a placed order can be marked delivered.")
    _set_delivered(order)


def poll_delivery(org_id: UUID, order_id: UUID, poll: int) -> None:
    """Check Swiggy for delivery. Runs as the poll-delivery job every few minutes."""
    with tenancy.scoped(org_id):
        order = SwiggyOrder.objects.filter(
            id=order_id, status=OrderStatus.PLACED
        ).first()
        if order is None:
            return
    if _call(get_provider().is_delivered, order.swiggy_order_id):
        with tenancy.scoped(org_id):
            order = lock_order(order_id)
            if order is not None and order.status == OrderStatus.PLACED:
                _set_delivered(order)
    elif poll < MAX_DELIVERY_POLLS:
        _schedule_delivery_poll(org_id, order_id, poll + 1)


def _earlier_orders(order: SwiggyOrder):
    return order.session.orders.filter(created_at__lt=order.created_at)


def _waits_for_earlier_order(order: SwiggyOrder) -> bool:
    """True if this provider orders one restaurant at a time and an earlier order of the
    same lunch hasn't been placed yet."""
    if not get_provider().sequential:
        return False
    return (
        _earlier_orders(order)
        .exclude(status__in=[OrderStatus.PLACED, OrderStatus.DELIVERED])
        .exists()
    )


def _start_next_order(order: SwiggyOrder) -> None:
    """After one order is placed, let the next waiting one fill its cart."""
    if not get_provider().sequential:
        return
    following = (
        order.session.orders.filter(
            status=OrderStatus.PENDING, created_at__gt=order.created_at
        )
        .order_by("created_at")
        .first()
    )
    if following is not None:
        payload = {"org_id": str(order.session.org_id), "order_id": str(following.id)}
        jobs.enqueue("place-order", payload, key=f"{following.id}-after-{order.id}")


def _place_request(order: SwiggyOrder) -> PlaceRequest:
    lunch = order.session
    items = order.items.order_by("restaurant_name", "dish_name")
    return PlaceRequest(
        order_id=order.id,
        host_id=lunch.host_id,
        delivery_address=lunch.delivery_address,
        lines=tuple(
            CartLine(
                item.restaurant_name,
                item.dish_name,
                item.quantity,
                item.note,
                item.restaurant_id,
            )
            for item in items
        ),
        total_paise=order.total_paise,
    )


def _call[T](action: Callable[..., T], *args) -> T:
    """Call the ordering provider, logging every call and how it went."""
    started = time.monotonic()
    outcome = "ok"
    try:
        return action(*args)
    except Exception as error:
        outcome = type(error).__name__
        raise
    finally:
        logger.info(
            "Swiggy call",
            extra={
                "fields": {
                    "action": action.__name__,
                    "target": str(getattr(args[0], "order_id", args[0])),
                    "outcome": outcome,
                    "ms": round((time.monotonic() - started) * 1000),
                }
            },
        )


def _save_steps(org_id: UUID, order_id: UUID, steps: list[dict]) -> None:
    """Save the bot's checkpoints at once, so the host watching the order sees them.

    Also renews the placement lease: a bot that is making progress is not crashed.
    """
    with _sync_orm_allowed(), tenancy.scoped(org_id):
        order = SwiggyOrder.objects.select_related("session").get(id=order_id)
        order.bot_steps = steps
        order.save(update_fields=["bot_steps", "updated_at"])
        board.publish(order.session)


@contextmanager
def _sync_orm_allowed() -> Iterator[None]:
    """Playwright's sync API runs an event loop inside the calling thread, and Django
    refuses database work while one is running. The bot reports its steps from inside
    that loop, so the write is allowed here (Django's documented switch, for this call
    only)."""
    name = "DJANGO_ALLOW_ASYNC_UNSAFE"
    before = os.environ.get(name)
    os.environ[name] = "true"
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = before


def _hand_to_host(org_id: UUID, order_id: UUID, reason: str) -> None:
    with tenancy.scoped(org_id):
        order = lock_order(order_id)
        if order is None or order.status != OrderStatus.PLACING:
            return
        order.status = OrderStatus.NEEDS_HOST
        order.last_error = reason
        order.save(update_fields=["status", "last_error", "updated_at"])
        board.publish(order.session)
        host_email, link = order.session.host.email, lunch_link(order.session)
    logger.warning(
        "Swiggy order handed to the host", extra={"fields": {"order_id": order_id}}
    )
    notify.email([host_email], "Your lunch order needs you", f"{reason}\n\n{link}")


def _set_placed(order: SwiggyOrder, swiggy_order_id: str) -> None:
    order.status = OrderStatus.PLACED
    order.swiggy_order_id = swiggy_order_id
    order.placed_at = timezone.now()
    order.last_error = ""
    order.save(
        update_fields=[
            "status",
            "swiggy_order_id",
            "placed_at",
            "last_error",
            "updated_at",
        ]
    )
    _refresh_lunch_status(order.session)
    if get_provider().tracks_delivery:
        _schedule_delivery_poll(order.session.org_id, order.id, 1)


def _set_delivered(order: SwiggyOrder) -> None:
    order.status = OrderStatus.DELIVERED
    order.save(update_fields=["status", "updated_at"])
    _refresh_lunch_status(order.session)


def _schedule_delivery_poll(org_id: UUID, order_id: UUID, poll: int) -> None:
    payload = {"org_id": str(org_id), "order_id": str(order_id), "poll": poll}
    at = timezone.now() + timedelta(seconds=settings.DELIVERY_POLL_SECONDS)
    jobs.enqueue("poll-delivery", payload, at=at, key=f"{order_id}-{poll}")


def _refresh_lunch_status(lunch: OrderSession) -> None:
    """Move the lunch on once all its Swiggy orders are placed, then delivered."""
    statuses = set(lunch.orders.values_list("status", flat=True))
    new_status: str
    if statuses == {OrderStatus.DELIVERED}:
        new_status = SessionStatus.DELIVERED
    elif statuses <= {OrderStatus.PLACED, OrderStatus.DELIVERED}:
        new_status = SessionStatus.PLACED
    else:
        new_status = lunch.status
    if new_status != lunch.status:
        lunch.status = new_status
        lunch.save(update_fields=["status", "updated_at"])
        # Ordered and arrived show on the board; people are only notified when someone
        # joins a party (lunches.services.tell_host_someone_joined).
    board.publish(lunch)
