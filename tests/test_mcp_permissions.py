"""What an AI assistant may do for each kind of person: the same as in the web app.

Every tool is called as a company host, a company member, a person with a public
email (a personal party), a host of another company and a stranger, against the
same lunch. The API decides; the assistant only passes it on."""

import uuid

import pytest

from apps.lunches.models import LocalMenu, OrderItem, OrderSession
from tests import test_mcp
from tests.conftest import HOST_PERMISSIONS, make_user
from tests.test_mcp import failing, run

world = test_mcp.world  # the faked Swiggy and place search, as a fixture here too

# The assistant's tools write from their own thread, so their writes must be
# committed to be seen here.
pytestmark = pytest.mark.django_db(transaction=True)

ITEMS = [{"name": "Meals", "price": 90}]
DENIED = "role doesn't let you"


@pytest.fixture
def other_host(db):
    from apps.accounts.models import Organization

    org = Organization.objects.create(name="Beta", email_domain="beta.test")
    return make_user(org, "Bela", HOST_PERMISSIONS)


@pytest.fixture
def stranger(db):
    return make_user(None, "Gus")


@pytest.fixture
def running(lunch, member):
    """The host's lunch (delivering somewhere known) with one of the member's picks."""
    lunch.address.lat, lunch.address.lng = 10.0105236, 76.3626
    lunch.address.save()
    OrderItem.objects.create(
        session=lunch,
        user=member,
        restaurant_name="Dosa Hut",
        restaurant_id="111",
        dish_name="Masala Dosa",
        quantity=1,
        price_paise=12000,
    )
    return lunch


# ---- what a member may NOT do (they pick; the host runs the lunch) ----


@pytest.mark.parametrize(
    "tool, arguments",
    [
        ("start_lunch", {"closes_at": "12:30", "delivery_place": "Infopark"}),
        ("add_local_menu", {"restaurant_name": "Amma", "items": ITEMS}),
        ("list_local_menus", {}),
    ],
)
def test_a_member_cannot_start_a_lunch_or_keep_local_menus(
    member, world, tool, arguments
):
    message = failing(member, tool, **arguments)

    assert DENIED in message or "Only hosts" in message
    assert not OrderSession.objects.exists() and not LocalMenu.objects.exists()


@pytest.mark.parametrize(
    "tool, arguments",
    [
        ("set_link_open", {"allow_new_people": False}),
        ("lock_lunch", {}),
        ("unlock_lunch", {}),
        ("confirm_lunch", {"confirm": True}),
        ("cancel_lunch", {"confirm": True}),
    ],
)
def test_a_member_cannot_run_the_lunch(member, running, tool, arguments):
    before = OrderSession.objects.values_list("status", "joining_open").get()

    message = failing(member, tool, lunch_id=str(running.id), **arguments)

    assert DENIED in message
    assert OrderSession.objects.values_list("status", "joining_open").get() == before


@pytest.mark.parametrize("tool", ["mark_order_placed", "mark_order_arrived"])
def test_a_member_cannot_record_orders(member, running, tool):
    assert DENIED in failing(member, tool, order_id=str(uuid.uuid4()))


# ---- what a member MAY do ----


def test_a_member_can_look_and_pick_and_finish(member, running, world):
    assert run(member, "whoami")["email"] == member.email
    assert run(member, "get_lunch", lunch_id=str(running.id))["status"] == "open"
    assert any(x["lunch_id"] == str(running.id) for x in run(member, "list_lunches"))
    assert run(member, "share_lunch", lunch_id=str(running.id))["share_link"]
    assert run(member, "find_restaurants", lunch_id=str(running.id))
    picked = run(
        member,
        "pick_dish",
        lunch_id=str(running.id),
        restaurant_id="12345",
        dish="Zinger Burger",
    )
    assert picked["item_id"]
    assert run(member, "finish_my_order", lunch_id=str(running.id))
    assert run(member, "my_notifications")["unread"] >= 0


def test_a_member_cannot_remove_someone_elses_pick(host, member, running):
    theirs = OrderItem.objects.create(
        session=running,
        user=host,
        restaurant_name="Dosa Hut",
        restaurant_id="111",
        dish_name="Tea",
        quantity=1,
        price_paise=1000,
    )

    message = failing(
        member, "remove_pick", lunch_id=str(running.id), item_id=str(theirs.id)
    )

    assert OrderItem.objects.filter(id=theirs.id).exists()
    assert message


# ---- the host ----


def test_a_host_can_run_their_own_lunch(host, running):
    assert run(host, "lock_lunch", lunch_id=str(running.id))["status"] == "locked"
    assert run(host, "unlock_lunch", lunch_id=str(running.id))["status"] == "open"
    assert run(host, "set_link_open", lunch_id=str(running.id), allow_new_people=False)


def test_a_host_needs_the_yes_before_anything_hard_to_undo(host, running):
    assert "Not confirmed" in failing(host, "cancel_lunch", lunch_id=str(running.id))
    assert "Not confirmed" in failing(host, "confirm_lunch", lunch_id=str(running.id))
    assert OrderSession.objects.get().status == "open"


# ---- other people's lunches and menus ----


def test_a_host_of_another_company_cannot_see_or_touch_the_lunch(other_host, running):
    lunch_id = str(running.id)

    for tool, arguments in [
        ("get_lunch", {}),
        ("lock_lunch", {}),
        ("cancel_lunch", {"confirm": True}),
        ("finish_my_order", {}),
    ]:
        assert failing(other_host, tool, lunch_id=lunch_id, **arguments)
    assert OrderSession.objects.get().status == "open"


def test_a_stranger_cannot_get_into_a_company_lunch_without_its_link_flow(
    stranger, running
):
    assert failing(stranger, "get_lunch", lunch_id=str(running.id))
    assert failing(stranger, "lock_lunch", lunch_id=str(running.id))


def test_a_person_with_a_public_email_runs_their_own_party(stranger, world):
    started = run(stranger, "start_lunch", closes_at="12:30", delivery_place="Infopark")

    assert run(stranger, "lock_lunch", lunch_id=started["lunch_id"])
    saved = run(stranger, "add_local_menu", restaurant_name="Amma", items=ITEMS)
    assert saved["menu_id"]


def test_local_menus_are_private_to_their_owner(host, other_host, world):
    saved = run(host, "add_local_menu", restaurant_name="Amma", items=ITEMS)

    assert run(other_host, "list_local_menus") == []
    assert failing(
        other_host, "delete_local_menu", menu_id=saved["menu_id"], confirm=True
    )
    assert LocalMenu.objects.filter(id=saved["menu_id"]).exists()
    # and it cannot be offered in someone else's party
    message = failing(
        other_host,
        "start_lunch",
        closes_at="12:30",
        delivery_place="Infopark",
        local_menu_ids=[saved["menu_id"]],
    )
    assert "isn't yours" in message or "local_menu_ids" in message
