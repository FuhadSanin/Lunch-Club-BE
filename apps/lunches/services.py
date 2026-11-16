"""The group lunch: start it, collect everyone's picks, lock them at the cutoff."""

from datetime import timedelta
from functools import partial
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ParseError, PermissionDenied, ValidationError

from apps.accounts import services as accounts
from apps.accounts.messages import not_allowed
from apps.accounts.models import (
    Notification,
    Organization,
    OrgKind,
    Permission,
    User,
)
from apps.core import jobs, notify, tenancy
from apps.core.errors import Conflict
from apps.lunches import board
from apps.lunches.models import (
    Order,
    OrderItem,
    OrderSession,
    SessionMember,
    SessionStatus,
)

# How long picks stay open when a lunch is unlocked after its cutoff has passed.
UNLOCK_GRACE = timedelta(minutes=30)


def lunch_link(lunch: OrderSession) -> str:
    # Opens this lunch (joining it first if needed); signed-out people sign in first.
    return f"{settings.APP_URL}/#/lunch/{lunch.id}"


# A lunch runs from when it starts until its food arrives or it is called off.
RUNNING = (
    SessionStatus.OPEN,
    SessionStatus.LOCKED,
    SessionStatus.CONFIRMED,
    SessionStatus.PLACED,
)


def _active(prefix: str = "") -> Q:
    """A lunch that isn't over: running, or arrived but not yet closed by its host.

    A lunch ends when the host finishes its last step ("Enjoy your meal"), which
    closes it, or when it is called off before anything is ordered.
    """
    return Q(**{f"{prefix}status__in": RUNNING}) | Q(
        **{
            f"{prefix}status": SessionStatus.DELIVERED,
            f"{prefix}closed_at__isnull": True,
        }
    )


def participants(lunch: OrderSession) -> QuerySet[User]:
    """Everyone in a lunch: its company, or a personal party's host and guests."""
    if lunch.org.kind == OrgKind.PERSONAL:
        guests = SessionMember.objects.filter(session=lunch).values("user_id")
        return User.objects.filter(Q(id=lunch.host_id) | Q(id__in=guests))
    return User.objects.filter(org_id=lunch.org_id)


def tell_participants(
    lunch: OrderSession, title: str, body: str, skip: User | None = None
) -> None:
    """Tell everyone in a lunch (but `skip`, who just did it): a line in each person's
    bell now, in the same transaction as the change, and a push once it commits.

    The bell's line has no link of its own: it is about the lunch the person is in,
    which is what the app shows. The push carries the lunch's link, because tapping
    it has to open the app somewhere.
    """
    link = lunch_link(lunch)
    people = participants(lunch)
    if skip is not None:
        people = people.exclude(id=skip.id)
    Notification.objects.bulk_create(
        [Notification(user=p, title=title, body=body) for p in people]
    )
    if lunch.org.kind == OrgKind.PERSONAL:
        send = partial(notify.push_to_lunch, lunch.id, title, body, link)
    else:
        send = partial(notify.push_to_company, lunch.org_id, title, body, link)
    transaction.on_commit(send)


# What the host is told about (nobody else is notified): someone joins their party,
# someone completes their order, and everyone is done picking.


def _tell_host(lunch: OrderSession, title: str, body: str) -> None:
    """A line in the host's bell now, and a push to the host's own devices on commit."""
    link = lunch_link(lunch)
    Notification.objects.create(user=lunch.host, title=title, body=body)
    tokens = list(lunch.host.devices.values_list("token", flat=True))
    transaction.on_commit(partial(notify.push_to_devices, tokens, title, body, link))


def _name(user: User) -> str:
    return user.name or user.email


def tell_host_someone_joined(lunch: OrderSession, guest: User) -> None:
    count = participants(lunch).count()
    where = f"“{lunch.name}” has" if lunch.name else "Your lunch has"
    _tell_host(
        lunch,
        f"{_name(guest)} joined your party",
        f"{where} {count} {'person' if count == 1 else 'people'} now.",
    )


def _after_finishing(lunch: OrderSession, user: User, *, was_done: bool) -> None:
    """Someone's picks just became final (completed, or skipped): tell the host who
    completed an order, and once, when that made everyone done."""
    if was_done:
        return
    everyone = participants(lunch)
    total = everyone.count()
    done = lunch.completed_by.filter(id__in=everyone.values("id")).count()
    skipped = not lunch.items.filter(user=user).exists()  # nothing picked: skipped
    if user.id != lunch.host_id and not skipped:
        _tell_host(
            lunch,
            f"{_name(user)} completed their order",
            f"{done} of {total} have picked.",
        )
    if total and done == total:
        _tell_host(
            lunch,
            "Everyone has picked",
            f"All {total} are in. Review the cart and lock the picks.",
        )


def subscribe_devices(user: User, lunch: OrderSession) -> None:
    """Have this person's devices hear a personal party's lunch (after commit)."""
    tokens = list(user.devices.values_list("token", flat=True))
    if tokens and lunch.org.kind == OrgKind.PERSONAL:
        lunch_id = lunch.id
        transaction.on_commit(lambda: notify.subscribe_to_lunch(tokens, lunch_id))


def running_party(user: User) -> OrderSession | None:
    """The lunch a personal-party person hosts or joined that isn't over, if any.

    Such a person is in one party at a time: to start their own they first finish the
    one they host (or call it off), or leave the one they joined.
    """
    hosted = OrderSession.objects.filter(_active(), host=user).first()
    if hosted is not None:
        return hosted
    joined = SessionMember.objects.filter(_active("session__"), user=user)
    member = joined.select_related("session").first()
    return member.session if member else None


def ensure_free(user: User) -> None:
    """Refuse a new party while this person is still in one."""
    if busy := running_party(user):
        what = "Finish" if busy.host_id == user.id else "Leave"
        raise Conflict(
            f"You're already in “{busy.name or 'a lunch'}”. {what} it first,"
            " then start a new one."
        )


def _ensure_company_free(org_id: UUID) -> None:
    """A company runs one lunch at a time: refuse a new one while one isn't over.

    The company's row is locked first, so two hosts starting at the same moment run one
    after the other and the second is refused."""
    Organization.objects.select_for_update().get(id=org_id)
    running = OrderSession.objects.filter(_active(), org_id=org_id).first()
    if running is not None:
        raise Conflict(
            f"“{running.name or 'A lunch'}” is still running. Close it first, then "
            "start a new one."
        )


def start(host: User, cutoff_at, address: dict, **extra) -> OrderSession:
    """Start a lunch delivered to `address` (its `address` text and, if known, `lat`
    and `lng`); `extra` is its name and the restaurants offered, if given.

    The address goes in the company's addresses (the one with that text is reused), and
    the lunch points at it."""
    org_id = host.org_id
    if org_id is None:  # views make a public-email host's party first
        raise Conflict("Set up or join your company first.")
    if host.org and host.org.kind == OrgKind.PERSONAL:
        ensure_free(host)
    else:
        _ensure_company_free(org_id)
    lunch = OrderSession.objects.create(
        org_id=org_id,
        host=host,
        cutoff_at=cutoff_at,
        address=accounts.save_address(
            org_id,
            address["address"],
            address.get("lat"),
            address.get("lng"),
            saved=False,
        )[0],
        **extra,
    )
    jobs.enqueue(
        "lock-lunch",
        {"org_id": str(org_id), "lunch_id": str(lunch.id)},
        at=cutoff_at,
        key=str(lunch.id),
    )
    board.publish(lunch)
    # No notification for a new lunch: people are only told when someone joins a party.
    subscribe_devices(host, lunch)
    return lunch


def join(user: User, lunch_id) -> OrderSession:
    """Take part in a lunch from its link. Safe to repeat.

    Your org's own lunch needs nothing. A personal party's lunch can be joined by anyone
    with the link while picks are open; a company's lunch only by the company's people.
    The lunch is read through `tenancy.joining`, which opens exactly this one lunch.
    """
    with tenancy.joining(lunch_id):
        lunch = get_object_or_404(
            OrderSession.objects.select_related("org", "host"), id=lunch_id
        )
        if lunch.org_id == user.org_id:
            if not user.has_perm(Permission.SESSION_READ):
                raise PermissionDenied(not_allowed(Permission.SESSION_READ))
            return lunch
        if lunch.org.kind != OrgKind.PERSONAL:
            raise PermissionDenied(
                f"This lunch belongs to {lunch.org.name}. Only people from "
                "that company can join it."
            )
        if SessionMember.objects.filter(session=lunch, user=user).exists():
            return lunch
        if lunch.status != SessionStatus.OPEN or lunch.cutoff_at <= timezone.now():
            raise ParseError("This lunch has closed.")
        if not lunch.joining_open:
            raise PermissionDenied(
                f"{lunch.host.name or 'The host'} has closed this lunch to new people. "
                "Ask them for a new link or to open it again."
            )
        SessionMember.objects.create(session=lunch, user=user, org_id=lunch.org_id)
        subscribe_devices(user, lunch)
        tell_host_someone_joined(lunch, user)
        board.publish(lunch)  # everyone's board shows the new person at once
    return lunch


def set_joining(lunch: OrderSession, open_: bool) -> None:
    """The host opens or closes the share link to new people. Expects `lunch` locked
    with select_for_update. Only a lunch still taking picks has a link to close."""
    if lunch.status != SessionStatus.OPEN:
        raise Conflict("Picks have closed, so nobody new can join anyway.")
    lunch.joining_open = open_
    lunch.save(update_fields=["joining_open", "updated_at"])
    board.publish(lunch)


def leave(lunch: OrderSession, user: User) -> None:
    """Leave a party joined by its link; any picks are dropped.

    The host closes their lunch instead, and a company's people can't leave their own
    company's lunch. Once the order is under way, someone with dishes in it stays
    until the food arrives, so the order never changes behind the host's back.
    """
    if lunch.org_id == user.org_id:
        raise Conflict(
            "You host this lunch. Close it instead."
            if lunch.host_id == user.id
            else "This is your company's lunch, so you can't leave it."
        )
    ordering = lunch.status in RUNNING and lunch.status != SessionStatus.OPEN
    if ordering and lunch.items.filter(user=user).exists():
        raise Conflict(
            "Your food is already being ordered. You can leave once it arrives."
        )
    lunch.items.filter(user=user).delete()
    lunch.completed_by.remove(user)
    SessionMember.objects.filter(session=lunch, user=user).delete()
    board.publish(lunch)


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
    _review_again(lunch, user, {item.restaurant_name})
    board.publish(lunch)
    return item


def update_item(lunch: OrderSession, item: OrderItem, fields: dict) -> OrderItem:
    _ensure_editable(lunch, item.user)
    restaurants = {item.restaurant_name}
    for name, value in fields.items():
        setattr(item, name, value)
    item.save()
    _review_again(lunch, item.user, restaurants | {item.restaurant_name})
    board.publish(lunch)
    return item


def remove_item(lunch: OrderSession, item: OrderItem) -> None:
    _ensure_editable(lunch, item.user)
    item.delete()
    _review_again(lunch, item.user, {item.restaurant_name})
    board.publish(lunch)


def skip(lunch: OrderSession, user: User) -> None:
    """Sit this lunch out; any picks are dropped. Final, like completing an order."""
    _ensure_editable(lunch, user)
    was_done = lunch.completed_by.filter(id=user.id).exists()
    _review_again(lunch, user, _restaurants_of(lunch, user))
    lunch.items.filter(user=user).delete()
    lunch.completed_by.add(user)  # completed with no dishes is what skipping is
    _after_finishing(lunch, user, was_done=was_done)
    board.publish(lunch)


def unskip(lunch: OrderSession, user: User) -> None:
    """Join back in after skipping. Skipping is final (see `skip`), so this only works
    once the host has reopened the person's order; before that, it says so."""
    _ensure_editable(lunch, user)
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
    changed = _restaurants_of(lunch, user) | {f["restaurant_name"] for f in items}
    lunch.items.filter(user=user).delete()
    OrderItem.objects.bulk_create(
        OrderItem(session=lunch, user=user, **fields) for fields in items
    )
    _review_again(lunch, user, changed)
    if complete:
        was_done = lunch.completed_by.filter(id=user.id).exists()
        lunch.completed_by.add(user)
        _after_finishing(lunch, user, was_done=was_done)
    board.publish(lunch)


def complete(lunch: OrderSession, user: User) -> None:
    """Make this person's picks final; only the host can reopen them. Safe to repeat."""
    _ensure_open(lunch)
    if not lunch.items.filter(user=user).exists():
        raise Conflict("Pick a dish first, or skip today.")
    was_done = lunch.completed_by.filter(id=user.id).exists()
    lunch.completed_by.add(user)
    _after_finishing(lunch, user, was_done=was_done)
    board.publish(lunch)


def reopen(lunch: OrderSession, person: User) -> None:
    """The host lets someone change a completed order or skipped day again."""
    _ensure_open(lunch)
    lunch.completed_by.remove(person)
    board.publish(lunch)


# ---------- the host's ticks while running the lunch ----------

# What can be ticked, the list on the lunch that keeps it, and whether its key is a
# restaurant's name or a person's id.
TICKS = {
    "reviewed_restaurant": ("reviewed_restaurants", "restaurant"),
    "reviewed_person": ("reviewed_people", "person"),
    "arrived": ("arrived_restaurants", "restaurant"),
    "handed_out": ("handed_out_people", "person"),
}


def tick(lunch: OrderSession, kind: str, key: str, done: bool) -> None:
    """Tick (or untick) one thing the host keeps track of while running a lunch.

    Only what is in the lunch can be ticked: a restaurant someone picked from, a person
    with dishes. Unticking never fails. Safe to repeat. Expects `lunch` locked with
    select_for_update, so two hosts ticking at once don't lose each other's ticks.
    """
    if lunch.status == SessionStatus.CANCELLED or lunch.closed_at is not None:
        raise Conflict("This lunch is over.")
    field, of = TICKS[kind]
    value: str | UUID = key
    if of == "person":
        try:
            value = UUID(key)
        except ValueError as error:
            raise ValidationError({"key": ["Give the person's id."]}) from error
    ticked = list(getattr(lunch, field))
    if done and value not in ticked:
        picked = (
            lunch.items.filter(user_id=value)
            if of == "person"
            else lunch.items.filter(restaurant_name=value)
        )
        if not picked.exists():
            raise ValidationError({"key": [f"No {of} with dishes in this lunch."]})
        ticked.append(value)
    elif not done and value in ticked:
        ticked.remove(value)
    else:
        return
    setattr(lunch, field, ticked)
    lunch.save(update_fields=[field, "updated_at"])
    board.publish(lunch)


def _restaurants_of(lunch: OrderSession, user: User) -> set[str]:
    return set(lunch.items.filter(user=user).values_list("restaurant_name", flat=True))


def _review_again(lunch: OrderSession, user: User, restaurants: set[str]) -> None:
    """Someone's picks changed: the host's review ticks for them, and for every
    restaurant in the old or new picks, are cleared so the host checks them again."""
    people = [p for p in lunch.reviewed_people if p != user.id]
    places = [r for r in lunch.reviewed_restaurants if r not in restaurants]
    if len(people) != len(lunch.reviewed_people) or len(places) != len(
        lunch.reviewed_restaurants
    ):
        lunch.reviewed_people, lunch.reviewed_restaurants = people, places
        lunch.save(
            update_fields=["reviewed_people", "reviewed_restaurants", "updated_at"]
        )


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
        order = Order.objects.create(
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


def close(lunch: OrderSession) -> None:
    """The host ends a lunch whose food has arrived. Safe to repeat.

    Expects `lunch` locked with select_for_update.
    """
    if lunch.status != SessionStatus.DELIVERED:
        raise Conflict("A lunch closes once its food has arrived.")
    if lunch.closed_at is None:
        lunch.closed_at = timezone.now()
        lunch.save(update_fields=["closed_at", "updated_at"])
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
