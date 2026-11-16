"""The host's ticks while running a lunch (reviewed, arrived, handed out) live on the
lunch, so every device and a second host see the same; and a company runs one lunch at
a time."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.lunches.models import OrderSession, SessionStatus
from tests.conftest import client_for, make_user

DISH = {
    "restaurant_name": "Dosa Hut",
    "restaurant_id": "111",
    "dish_name": "Masala Dosa",
    "quantity": 1,
    "price_paise": 12000,
}


def pick(user, lunch, **overrides):
    return client_for(user).post(
        f"/api/lunches/{lunch.id}/items", {**DISH, **overrides}, format="json"
    )


def tick(user, lunch, kind, key, done=True):
    return client_for(user).put(
        f"/api/lunches/{lunch.id}/ticks",
        {"kind": kind, "key": str(key), "done": done},
        format="json",
    )


def test_the_hosts_ticks_are_on_the_board_for_every_device(lunch, host, member):
    pick(member, lunch)

    tick(host, lunch, "reviewed_restaurant", "Dosa Hut")
    tick(host, lunch, "reviewed_person", member.id)
    tick(host, lunch, "arrived", "Dosa Hut")
    response = tick(host, lunch, "handed_out", member.id)

    assert response.status_code == 200
    # Another device, another person: the same ticks.
    board = client_for(member).get(f"/api/lunches/{lunch.id}").json()
    assert board["reviewed_restaurants"] == ["Dosa Hut"]
    assert board["reviewed_people"] == [str(member.id)]
    assert board["arrived_restaurants"] == ["Dosa Hut"]
    assert board["handed_out_people"] == [str(member.id)]


def test_a_second_host_sees_and_undoes_a_tick(lunch, host, admin, member):
    pick(member, lunch)
    tick(host, lunch, "arrived", "Dosa Hut")

    again = tick(admin, lunch, "arrived", "Dosa Hut")  # ticking twice is harmless
    undone = tick(admin, lunch, "arrived", "Dosa Hut", done=False)

    assert again.json()["arrived_restaurants"] == ["Dosa Hut"]
    assert undone.json()["arrived_restaurants"] == []


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("arrived", "A restaurant nobody picked from"),
        ("handed_out", "00000000-0000-0000-0000-000000000000"),
        ("handed_out", "not-an-id"),
        ("made_up", "Dosa Hut"),
    ],
)
def test_only_what_is_in_the_lunch_can_be_ticked(lunch, host, member, kind, key):
    pick(member, lunch)

    assert tick(host, lunch, kind, key).status_code == 400


def test_unticking_something_no_longer_in_the_lunch_still_works(lunch, host, member):
    pick(member, lunch)
    tick(host, lunch, "arrived", "Dosa Hut")
    lunch.items.all().delete()

    response = tick(host, lunch, "arrived", "Dosa Hut", done=False)

    assert response.status_code == 200
    assert response.json()["arrived_restaurants"] == []


def test_members_cannot_tick(lunch, member):
    pick(member, lunch)

    assert tick(member, lunch, "arrived", "Dosa Hut").status_code == 403


def test_another_companys_lunch_cannot_be_ticked(lunch, member, other_company):
    pick(member, lunch)
    outsider = make_user(other_company, "Otto", {"session_update", "session_read"})

    assert tick(outsider, lunch, "arrived", "Dosa Hut").status_code == 404


def test_a_lunch_that_is_over_takes_no_more_ticks(lunch, host, member):
    pick(member, lunch)
    OrderSession.objects.filter(id=lunch.id).update(status=SessionStatus.CANCELLED)

    assert tick(host, lunch, "arrived", "Dosa Hut").status_code == 409


def test_changing_a_pick_clears_its_review_ticks(lunch, host, member):
    other = make_user(lunch.org, "Gus")
    pick(member, lunch)
    pick(other, lunch, restaurant_name="Paragon", dish_name="Biryani")
    for kind, key in [
        ("reviewed_restaurant", "Dosa Hut"),
        ("reviewed_restaurant", "Paragon"),
        ("reviewed_person", member.id),
        ("reviewed_person", other.id),
    ]:
        tick(host, lunch, kind, key)

    pick(member, lunch, dish_name="Idli")  # Meera changes her order

    board = client_for(host).get(f"/api/lunches/{lunch.id}").json()
    # Her order and her restaurant are to be checked again; Gus's are untouched.
    assert board["reviewed_restaurants"] == ["Paragon"]
    assert board["reviewed_people"] == [str(other.id)]


def test_skipping_and_replacing_picks_clear_review_ticks_too(lunch, host, member):
    pick(member, lunch)
    tick(host, lunch, "reviewed_restaurant", "Dosa Hut")
    tick(host, lunch, "reviewed_person", member.id)

    client_for(member).put(
        f"/api/lunches/{lunch.id}/picks",
        {"items": [{**DISH, "restaurant_name": "Paragon"}], "complete": False},
        format="json",
    )
    replaced = client_for(host).get(f"/api/lunches/{lunch.id}").json()
    tick(host, lunch, "reviewed_restaurant", "Paragon")
    client_for(member).put(f"/api/lunches/{lunch.id}/skip")
    skipped = client_for(host).get(f"/api/lunches/{lunch.id}").json()

    assert replaced["reviewed_restaurants"] == replaced["reviewed_people"] == []
    assert skipped["reviewed_restaurants"] == []


# --- one lunch at a time ----------------------------------------------------------


def start(user, name="Lunch"):
    cutoff = timezone.now() + timedelta(hours=1)
    return client_for(user).post(
        "/api/lunches",
        {
            "cutoff_at": cutoff.isoformat(),
            "address": {"address": "Office"},
            "name": name,
        },
        format="json",
    )


def test_a_company_cannot_start_a_second_lunch_while_one_is_running(host, admin):
    assert start(host, "Friday biryani").status_code == 201

    by_the_same_host = start(host)
    by_another_host = start(admin)

    assert by_the_same_host.status_code == by_another_host.status_code == 409
    assert "Friday biryani" in by_another_host.json()["detail"]
    assert OrderSession.objects.count() == 1


@pytest.mark.parametrize("ending", ["cancelled", "closed"])
def test_a_company_can_start_again_once_its_lunch_is_over(host, ending):
    first = OrderSession.objects.get(id=start(host).json()["id"])
    if ending == "cancelled":
        first.status = SessionStatus.CANCELLED
    else:
        first.status, first.closed_at = SessionStatus.DELIVERED, timezone.now()
    first.save()

    assert start(host).status_code == 201


def test_food_that_arrived_but_is_not_closed_still_blocks_a_new_lunch(host):
    first = start(host).json()
    OrderSession.objects.filter(id=first["id"]).update(status=SessionStatus.DELIVERED)

    assert start(host).status_code == 409


def test_another_companys_running_lunch_does_not_block(host, other_company):
    theirs = make_user(other_company, "Otto", {"session_create", "session_read"})
    assert start(theirs).status_code == 201

    assert start(host).status_code == 201
