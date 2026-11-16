"""The receipt of a lunch: only for the host who ran it."""

import pytest

from apps.lunches.models import OrderItem
from tests.conftest import client_for, make_user


@pytest.fixture
def served(lunch, host, member):
    lunch.name = "Friday biryani"
    lunch.offered_restaurants = [
        {
            "id": "111",
            "name": "Dosa Hut",
            "area": "Kakkanad",
            "cuisines": ["South Indian"],
            "rating": 4.4,
            "eta": "25-30 min",
            "distance_km": 1.2,
        }
    ]
    lunch.save()
    for user, dish, qty, price in [
        (host, "Masala Dosa", 2, 12000),
        (member, "Masala Dosa", 1, 12000),
        (member, "Filter Coffee", 1, 4000),
    ]:
        OrderItem.objects.create(
            session=lunch,
            user=user,
            restaurant_name="Dosa Hut",
            restaurant_id="111",
            dish_name=dish,
            quantity=qty,
            price_paise=price,
            note="less oil" if user == member and dish == "Masala Dosa" else "",
        )
    return lunch


def test_the_host_sees_what_each_person_ordered_and_owes(served, host):
    got = client_for(host).get(f"/api/lunches/{served.id}/history")

    assert got.status_code == 200
    receipt = got.json()
    assert receipt["name"] == "Friday biryani"
    assert receipt["total_paise"] == 2 * 12000 + 12000 + 4000
    owes = {p["user"]["name"]: p["owes_paise"] for p in receipt["people"]}
    assert owes == {"Hari": 24000, "Meera": 16000}
    meera = next(p for p in receipt["people"] if p["user"]["name"] == "Meera")
    assert {i["dish_name"] for i in meera["items"]} == {"Masala Dosa", "Filter Coffee"}
    assert any(i["note"] == "less oil" for i in meera["items"])


def test_it_shows_the_restaurant_with_its_dishes_and_details(served, host):
    receipt = client_for(host).get(f"/api/lunches/{served.id}/history").json()

    (restaurant,) = receipt["restaurants"]
    assert restaurant["name"] == "Dosa Hut"
    assert restaurant["area"] == "Kakkanad"
    assert restaurant["cuisines"] == ["South Indian"]
    assert restaurant["total_paise"] == 40000
    dosa = next(d for d in restaurant["dishes"] if d["dish_name"] == "Masala Dosa")
    assert dosa["quantity"] == 3
    assert sorted(dosa["people"]) == ["Hari", "Meera"]


def test_a_lunch_without_a_name_has_an_empty_name(lunch, host):
    receipt = client_for(host).get(f"/api/lunches/{lunch.id}/history").json()

    assert receipt["name"] == ""
    assert receipt["people"] == [] and receipt["total_paise"] == 0


def test_a_member_of_the_company_cannot_see_it(served, member):
    got = client_for(member).get(f"/api/lunches/{served.id}/history")

    assert got.status_code == 403
    assert "hosted" in got.json()["detail"]


def test_another_host_of_the_company_cannot_see_it_either(served, company):
    from tests.conftest import HOST_PERMISSIONS

    other = make_user(company, "Hema", HOST_PERMISSIONS)

    assert client_for(other).get(f"/api/lunches/{served.id}/history").status_code == 403


def test_someone_outside_gets_nothing(served, db):
    stranger = make_user(None, "Stranger")

    assert client_for(stranger).get(
        f"/api/lunches/{served.id}/history"
    ).status_code in (
        403,
        404,
    )


def test_it_needs_a_sign_in(served, client):
    assert client.get(f"/api/lunches/{served.id}/history").status_code == 401
