"""Local menus: a host's own list of a restaurant that isn't on Swiggy."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.lunches import services as lunch_services
from apps.lunches.models import LocalMenu, Order, OrderItem, OrderSession, OrderStatus
from apps.ordering.providers import get_provider
from tests.conftest import HOST_PERMISSIONS, client_for, make_user

DOT = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def menu_body(**over):
    return {
        "name": "Amma's Mess",
        "image": "https://example.test/amma.jpg",
        "rating": 4.3,
        "distance_km": 1.4,
        "items": [
            {"name": "Meals", "price_paise": 9000, "image": DOT, "veg": True},
            {"name": "Fish fry", "price_paise": 15000},
        ],
        **over,
    }


def create(user, **over):
    return client_for(user).post("/api/local-menus", menu_body(**over), format="json")


@pytest.fixture
def served(host):
    return create(host).json()


def test_a_host_keeps_a_menu_with_its_dishes(host):
    made = create(host)

    assert made.status_code == 201
    menu = made.json()
    assert menu["name"] == "Amma's Mess" and menu["rating"] == 4.3
    assert [i["name"] for i in menu["items"]] == ["Meals", "Fish fry"]
    assert all(i["id"] for i in menu["items"])
    assert client_for(host).get("/api/local-menus").json()[0]["id"] == menu["id"]


@pytest.mark.parametrize(
    "over, field",
    [
        ({"name": ""}, "name"),
        ({"items": []}, "items"),
        ({"items": [{"name": "", "price_paise": 100}]}, "items"),
        ({"items": [{"name": "Tea"}]}, "items"),
        ({"items": [{"name": "Tea", "price_paise": 0}]}, "items"),
        ({"rating": 6}, "rating"),
        ({"distance_km": -1}, "distance_km"),
        ({"image": "http://example.test/not-https.jpg"}, "image"),
        ({"image": "data:text/html;base64,AAAA"}, "image"),
        ({"image": "data:image/png;base64," + "A" * 90_000}, "image"),
        (
            {"items": [{"name": "Tea", "price_paise": 100}] * 2},
            "items",
        ),
    ],
)
def test_the_name_and_a_dish_with_a_price_are_required(host, over, field):
    made = create(host, **over)

    assert made.status_code == 400
    assert field in made.json()


def test_only_the_rest_is_optional(host):
    made = create(host, image="", rating=None, distance_km=None)

    assert made.status_code == 201


def test_a_menu_is_only_its_owners(served, host, company):
    other = make_user(company, "Hema", HOST_PERMISSIONS)

    assert client_for(other).get("/api/local-menus").json() == []
    got = client_for(other).get(f"/api/local-menus/{served['id']}")
    assert got.status_code == 404
    assert (
        client_for(other).delete(f"/api/local-menus/{served['id']}").status_code == 404
    )


def test_a_member_cannot_keep_menus(member):
    assert client_for(member).get("/api/local-menus").status_code == 403
    assert create(member).status_code == 403


def test_a_gmail_person_can_keep_menus_before_their_first_party(db):
    person = make_user(None, "Gus")

    assert create(person).status_code == 201


def test_a_menu_can_be_changed_and_deleted(served, host):
    client = client_for(host)

    changed = client.put(
        f"/api/local-menus/{served['id']}",
        menu_body(name="Amma's Kitchen", items=[{"name": "Tea", "price_paise": 1000}]),
        format="json",
    )

    assert changed.status_code == 200
    assert changed.json()["name"] == "Amma's Kitchen"
    assert len(changed.json()["items"]) == 1
    assert client.delete(f"/api/local-menus/{served['id']}").status_code == 204
    assert client.get("/api/local-menus").json() == []


def test_at_most_thirty_menus(host):
    for i in range(30):
        assert create(host, name=f"Place {i}").status_code == 201

    assert create(host, name="One more").status_code == 409


def test_an_access_token_may_use_local_menus(host):
    from rest_framework.test import APIClient

    from apps.accounts import services

    token = services.issue_api_token(host, "Claude", timedelta(hours=1))[1]
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    assert (
        client.post("/api/local-menus", menu_body(), format="json").status_code == 201
    )


def start(user, ids, **over):
    return client_for(user).post(
        "/api/lunches",
        {
            "cutoff_at": (timezone.now() + timedelta(hours=1)).isoformat(),
            "address": {"address": "Tower A", "lat": 10.0, "lng": 76.3},
            "local_menu_ids": ids,
            **over,
        },
        format="json",
    )


def test_a_party_started_with_a_local_menu_offers_its_dishes(served, host):
    made = start(host, [served["id"]])

    assert made.status_code == 201
    (offered,) = made.json()["offered_restaurants"]
    assert offered["id"] == f"local-{served['id']}" and offered["local"] is True
    assert offered["name"] == "Amma's Mess" and offered["rating"] == 4.3
    assert {i["name"]: i["price_paise"] for i in offered["items"]} == {
        "Meals": 9000,
        "Fish fry": 15000,
    }


def test_it_comes_next_to_the_swiggy_restaurants_the_host_chose(served, host):
    swiggy = {"id": "111", "name": "Dosa Hut"}

    made = start(host, [served["id"]], offered_restaurants=[swiggy])

    assert [r["name"] for r in made.json()["offered_restaurants"]] == [
        "Dosa Hut",
        "Amma's Mess",
    ]


def test_someone_elses_menu_cannot_be_offered(served, host, company):
    other = make_user(company, "Hema", HOST_PERMISSIONS)

    refused = start(other, [served["id"]])

    assert refused.status_code == 400
    assert "local_menu_ids" in refused.json()
    assert not OrderSession.objects.exists()


def test_a_party_keeps_its_copy_when_the_menu_changes(served, host):
    made = start(host, [served["id"]]).json()
    client_for(host).put(
        f"/api/local-menus/{served['id']}",
        menu_body(items=[{"name": "Tea", "price_paise": 1000}]),
        format="json",
    )

    board = client_for(host).get(f"/api/lunches/{made['id']}").json()

    assert len(board["offered_restaurants"][0]["items"]) == 2


def pick_local(user, lunch, restaurant_id, name="Amma's Mess"):
    return client_for(user).post(
        f"/api/lunches/{lunch['id']}/items",
        {
            "restaurant_name": name,
            "restaurant_id": restaurant_id,
            "dish_name": "Meals",
            "quantity": 2,
            "price_paise": 9000,
        },
        format="json",
    )


def test_a_local_dish_can_be_picked_and_shows_in_the_cart_without_swiggy(served, host):
    lunch = start(host, [served["id"]]).json()

    picked = pick_local(host, lunch, f"local-{served['id']}")

    assert picked.status_code == 201
    board = client_for(host).get(f"/api/lunches/{lunch['id']}").json()
    assert board["items"][0]["restaurant_id"] == f"local-{served['id']}"
    lunch_services.lock(OrderSession.objects.get())
    cart = (
        client_for(host)
        .get(f"/api/lunches/{lunch['id']}")
        .json()["orders"][0]["carts"][0]
    )
    assert cart["local"] is True and cart["swiggy_url"] == ""


def test_a_local_restaurant_gets_its_own_order_apart_from_swiggy(
    served, host, member, settings
):
    settings.MAX_RESTAURANTS_PER_ORDER = 5
    lunch = start(host, [served["id"]]).json()
    session = OrderSession.objects.get()
    for rid, name in ((f"local-{served['id']}", "Amma's Mess"), ("111", "Dosa Hut")):
        OrderItem.objects.create(
            session=session,
            user=host,
            restaurant_name=name,
            restaurant_id=rid,
            dish_name="Meals",
            quantity=1,
            price_paise=9000,
        )

    lunch_services.lock(session)

    orders = list(Order.objects.filter(session=session))
    assert len(orders) == 2
    assert sorted(o.restaurant_count for o in orders) == [1, 1]
    assert lunch["id"]


def test_a_local_order_is_handed_to_the_host_not_to_swiggy(
    served, host, fake_provider, django_capture_on_commit_callbacks
):
    lunch = start(host, [served["id"]]).json()
    pick_local(host, lunch, f"local-{served['id']}")
    session = OrderSession.objects.get()
    lunch_services.lock(session)
    get_provider.cache_clear()

    with django_capture_on_commit_callbacks(execute=True):
        client_for(host).post(f"/api/lunches/{lunch['id']}/confirm")

    order = Order.objects.get()
    assert order.status == OrderStatus.NEEDS_HOST
    assert "local menu" in order.last_error
    assert "place" not in fake_provider.calls
    # The host places it with the restaurant and marks it ordered, like any order.
    placed = client_for(host).post(f"/api/orders/{order.id}/placed", {}, format="json")
    assert placed.status_code == 200


def test_the_swiggy_regex_still_refuses_made_up_ids(host, lunch):
    made = client_for(host).post(
        f"/api/lunches/{lunch.id}/items",
        {
            "restaurant_name": "X",
            "restaurant_id": "local-nonsense",
            "dish_name": "Y",
            "quantity": 1,
            "price_paise": 100,
        },
        format="json",
    )

    assert made.status_code == 400


def test_deleting_a_menu_does_not_touch_started_parties(served, host):
    start(host, [served["id"]])

    client_for(host).delete(f"/api/local-menus/{served['id']}")

    assert OrderSession.objects.get().offered_restaurants[0]["name"] == "Amma's Mess"
    assert not LocalMenu.objects.exists()
