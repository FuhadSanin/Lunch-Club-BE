from apps.lunches.checkout import checkout_carts, swiggy_url
from apps.lunches.models import OrderItem
from tests.conftest import make_user


def add(lunch, user, restaurant, dish, quantity=1, price=10000, **extra):
    return OrderItem.objects.create(
        session=lunch,
        user=user,
        restaurant_name=restaurant,
        dish_name=dish,
        quantity=quantity,
        price_paise=price,
        **extra,
    )


def test_each_checkout_lists_what_to_add_in_swiggy(lunch, host, member):
    items = [
        add(lunch, host, "Dosa Hut", "Masala Dosa", 2, 12000, restaurant_id="111"),
        add(lunch, member, "Dosa Hut", "Masala Dosa", 1, 12000, note="less spicy"),
        add(lunch, member, "Dosa Hut", "Vada", 1, 5000),
        add(lunch, host, "Biryani Co", "Chicken Biryani", 1, 25000),
    ]

    carts = checkout_carts(OrderItem.objects.filter(id__in=[i.id for i in items]))

    assert [c["restaurant_name"] for c in carts] == ["Biryani Co", "Dosa Hut"]
    biryani, dosa = carts
    assert biryani["swiggy_url"].startswith("https://www.swiggy.com/search?query=")
    assert biryani["restaurant_id"] == ""
    assert dosa["swiggy_url"] == "https://www.swiggy.com/menu/111"
    assert dosa["restaurant_id"] == "111"
    assert dosa["total_paise"] == 2 * 12000 + 12000 + 5000
    masala = next(d for d in dosa["dishes"] if d["name"] == "Masala Dosa")
    assert masala["quantity"] == 3
    assert sorted(masala["people"]) == ["Hari", "Meera"]
    assert dosa["cooking_note"].splitlines() == [
        "Hari - 2× Masala Dosa",
        "Meera - Masala Dosa, Vada · Note: less spicy",
    ]


def test_a_person_without_a_name_is_listed_by_email(lunch, company):
    nameless = make_user(company, "Nameless")
    nameless.name = ""
    nameless.save()
    add(lunch, nameless, "Dosa Hut", "Vada")

    (cart,) = checkout_carts(OrderItem.objects.select_related("user"))

    assert cart["cooking_note"] == "nameless@acme.com - Vada"


def test_a_restaurant_id_must_be_swiggys(lunch, member):
    from tests.conftest import client_for

    body = {
        "restaurant_name": "Dosa Hut",
        "restaurant_id": "not-a-number",
        "dish_name": "Vada",
        "quantity": 1,
        "price_paise": 5000,
    }

    response = client_for(member).post(
        f"/api/lunches/{lunch.id}/items", body, format="json"
    )

    assert response.status_code == 400
    assert "restaurant_id" in response.json()
    assert swiggy_url("A B", "") == "https://www.swiggy.com/search?query=A%20B"
