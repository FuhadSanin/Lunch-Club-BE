"""The group lunch: start it, collect everyone's picks, lock them at the cutoff."""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core import jobs, notify
from apps.core.errors import Conflict
from apps.lunches import board
from apps.lunches.models import OrderItem, OrderSession, SessionStatus, SwiggyOrder

# How long picks stay open when a lunch is unlocked after its cutoff has passed.
UNLOCK_GRACE = timedelta(minutes=30)


def lunch_link(lunch: OrderSession) -> str:
    # The web app opens the running lunch; signed-out people sign in first.
    return f"{settings.APP_URL}/#/pick"


def start(host: User, cutoff_at, delivery_address: str) -> OrderSession:
    org_id = host.org_id
    if org_id is None:  # views only let people with a company in
        raise Conflict("Set up or join your company first.")
    lunch = OrderSession.objects.create(
        org_id=org_id,
        host=host,
        cutoff_at=cutoff_at,
        delivery_address=delivery_address,
    )
    jobs.enqueue(
        "lock-lunch",
        {"org_id": str(org_id), "lunch_id": str(lunch.id)},
        at=cutoff_at,
        key=str(lunch.id),
    )
    board.publish(lunch)
    cutoff = timezone.localtime(cutoff_at).strftime("%I:%M %p").lstrip("0")
    transaction.on_commit(
        lambda: notify.push_to_company(
            org_id,
            f"{host.name or 'Your host'} started a lunch",
            f"Pick your food by {cutoff}.",
            lunch_link(lunch),
        )
    )
    return lunch


def _ensure_open(lunch: OrderSession) -> None:
    if lunch.status != SessionStatus.OPEN or timezone.now() >= lunch.cutoff_at:
        raise Conflict("Picks for this lunch are closed.")


def _ensure_editable(lunch: OrderSession, user: User) -> None:
    """Picks are open, and this person hasn't completed their order or skipped."""
    _ensure_open(lunch)
    if lunch.completed_by.filter(id=user.id).exists():
        raise Conflict("Your order is complete. Ask the host to reopen it.")


def add_item(lunch: OrderSession, user: User, fields: dict) -> OrderItem:
    """Expects `lunch` locked with select_for_update so picks can't race the cutoff."""
    _ensure_editable(lunch, user)
    item = OrderItem.objects.create(session=lunch, user=user, **fields)
    lunch.skipped_by.remove(user)
    board.publish(lunch)
    return item


def update_item(lunch: OrderSession, item: OrderItem, fields: dict) -> OrderItem:
    _ensure_editable(lunch, item.user)
    for name, value in fields.items():
        setattr(item, name, value)
    item.save()
    board.publish(lunch)
    return item


def remove_item(lunch: OrderSession, item: OrderItem) -> None:
    _ensure_editable(lunch, item.user)
    item.delete()
    board.publish(lunch)


def skip(lunch: OrderSession, user: User) -> None:
    """Sit this lunch out; any picks are dropped. Final, like completing an order."""
    _ensure_editable(lunch, user)
    lunch.items.filter(user=user).delete()
    lunch.skipped_by.add(user)
    lunch.completed_by.add(user)
    board.publish(lunch)


def unskip(lunch: OrderSession, user: User) -> None:
    _ensure_editable(lunch, user)
    lunch.skipped_by.remove(user)
    board.publish(lunch)


def replace_picks(
    lunch: OrderSession, user: User, items: list[dict], *, complete: bool
) -> None:
    """Make this person's picks exactly `items`, and optionally complete them, at once.

    One request instead of one per dish. Expects `lunch` locked with select_for_update.
    """
    _ensure_editable(lunch, user)
    if complete and not items:
        raise Conflict("Pick a dish first, or skip today.")
    lunch.items.filter(user=user).delete()
    OrderItem.objects.bulk_create(
        OrderItem(session=lunch, user=user, **fields) for fields in items
    )
    lunch.skipped_by.remove(user)
    if complete:
        lunch.completed_by.add(user)
    board.publish(lunch)


def complete(lunch: OrderSession, user: User) -> None:
    """Make this person's picks final; only the host can reopen them. Safe to repeat."""
    _ensure_open(lunch)
    if not lunch.items.filter(user=user).exists():
        raise Conflict("Pick a dish first, or skip today.")
    lunch.completed_by.add(user)
    board.publish(lunch)


def reopen(lunch: OrderSession, person: User) -> None:
    """The host lets someone change a completed order or skipped day again."""
    _ensure_open(lunch)
    lunch.completed_by.remove(person)
    lunch.skipped_by.remove(person)
    board.publish(lunch)


def lock(lunch: OrderSession) -> None:
    """Close picks and split them into Swiggy checkouts. Safe to call more than once.

    Expects `lunch` locked with select_for_update.
    """
    if lunch.status != SessionStatus.OPEN:
        return
    items = list(lunch.items.all())
    if not items:
        lunch.status = SessionStatus.CANCELLED
        lunch.save(update_fields=["status", "updated_at"])
        board.publish(lunch)
        return

    restaurants = sorted({item.restaurant_name for item in items})
    size = settings.MAX_RESTAURANTS_PER_ORDER
    for start_at in range(0, len(restaurants), size):
        batch = set(restaurants[start_at : start_at + size])
        batch_items = [item for item in items if item.restaurant_name in batch]
        order = SwiggyOrder.objects.create(
            session=lunch,
            restaurant_count=len(batch),
            total_paise=sum(item.total_paise for item in batch_items),
        )
        OrderItem.objects.filter(id__in=[item.id for item in batch_items]).update(
            order=order
        )

    lunch.status = SessionStatus.LOCKED
    lunch.save(update_fields=["status", "updated_at"])
    board.publish(lunch)
    host_email, link = lunch.host.email, lunch_link(lunch)
    transaction.on_commit(
        lambda: notify.email(
            [host_email],
            "Lunch picks are locked: confirm the order",
            f"Everyone's picks are in. Check the cart and confirm the order: {link}",
        )
    )


def unlock(lunch: OrderSession, cutoff_at=None) -> None:
    """Reopen picks after locking, before the cart is confirmed.

    The checkouts made at locking are dropped (the next lock makes them again). A
    cutoff that has passed moves 30 minutes ahead unless a new one is given, so
    people can still pick.
    Expects `lunch` locked with select_for_update.
    """
    if lunch.status != SessionStatus.LOCKED:
        raise Conflict("Only a locked lunch that isn't ordered yet can be unlocked.")
    lunch.orders.all().delete()
    if cutoff_at is None and lunch.cutoff_at <= timezone.now():
        cutoff_at = timezone.now() + UNLOCK_GRACE
    if cutoff_at is not None:
        lunch.cutoff_at = cutoff_at
    lunch.status = SessionStatus.OPEN
    lunch.save(update_fields=["status", "cutoff_at", "updated_at"])
    jobs.enqueue(
        "lock-lunch",
        {"org_id": str(lunch.org_id), "lunch_id": str(lunch.id)},
        at=lunch.cutoff_at,
        key=f"{lunch.id}-{lunch.cutoff_at:%Y%m%d%H%M}",
    )
    board.publish(lunch)


def cancel(lunch: OrderSession) -> None:
    """Call off a lunch before it is confirmed.

    Expects `lunch` locked with select_for_update.
    """
    if lunch.status not in (SessionStatus.OPEN, SessionStatus.LOCKED):
        raise Conflict("This lunch has already been ordered.")
    lunch.orders.all().delete()
    lunch.status = SessionStatus.CANCELLED
    lunch.save(update_fields=["status", "updated_at"])
    board.publish(lunch)
