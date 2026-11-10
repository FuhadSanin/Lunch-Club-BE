import httpx
import pytest
from django.core.cache import cache

from apps.catalog import swiggy
from tests.conftest import client_for, make_user

FOOD = "type.googleapis.com/swiggy.presentation.food.v2"
OFFICE = {"lat": "9.9312", "lng": "76.2673"}

RESTAURANT_INFO = {
    "id": "263840",
    "name": "Paragon",
    "areaName": "Kaloor",
    "cuisines": ["Kerala", "Biryani"],
    "avgRating": 4.5,
    "costForTwoMessage": "₹600 for two",
    "sla": {"slaString": "25-30 mins", "lastMileTravel": 2.1},
    "availability": {"opened": True},
    "cloudinaryImageId": "paragon.jpg",
}

LISTING = {
    "data": {
        "cards": [
            {
                "card": {
                    "gridElements": {
                        "restaurants": [
                            {"info": RESTAURANT_INFO},
                            {
                                "info": {
                                    **RESTAURANT_INFO,
                                    "id": "1001",
                                    "name": "Kayees",
                                }
                            },
                        ]
                    }
                }
            }
        ]
    }
}

MENU = {
    "data": {
        "cards": [
            {
                "card": {
                    "card": {
                        "@type": f"{FOOD}.Restaurant",
                        "info": {**RESTAURANT_INFO, "city": "Kochi"},
                    }
                }
            },
            {
                "card": {
                    "card": {
                        "@type": f"{FOOD}.RestaurantAddress",
                        "completeAddress": "Kaloor, Kochi",
                    }
                }
            },
            {
                "groupedCard": {
                    "cards": [
                        {
                            "card": {
                                "card": {
                                    "title": "Biryani",
                                    "itemCards": [
                                        {
                                            "card": {
                                                "info": {
                                                    "id": "11",
                                                    "name": "Chicken Biryani",
                                                    "price": 32000,
                                                    "isVeg": 0,
                                                }
                                            }
                                        },
                                        {
                                            "card": {
                                                "info": {
                                                    "id": "12",
                                                    "name": "Veg Biryani",
                                                    "defaultPrice": 22000,
                                                    "isVeg": 1,
                                                    "inStock": 0,
                                                }
                                            }
                                        },
                                    ],
                                }
                            }
                        }
                    ]
                }
            },
        ]
    }
}

DISHES = {
    "data": {
        "cards": [
            {
                "card": {
                    "card": {
                        "@type": f"{FOOD}.DishGroup",
                        "restaurant": {"info": RESTAURANT_INFO},
                        "dishes": [
                            {
                                "info": {
                                    "id": "11",
                                    "name": "Chicken Biryani",
                                    "price": 32000,
                                    "isVeg": 0,
                                }
                            }
                        ],
                    }
                }
            }
        ]
    }
}


@pytest.fixture
def swiggy_api(monkeypatch):
    """Answers like Swiggy; tests steer it with `routes` (path -> JSON or status)."""
    calls: list[str] = []
    routes = {
        "/dapi/restaurants/list/v5": LISTING,
        "/dapi/restaurants/search/v3": LISTING,
        "/dapi/menu/pl": MENU,
        "/": 200,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if (
            request.url.path == "/dapi/restaurants/search/v3"
            and request.url.params.get("selectedPLTab") == "DISH"
        ):
            return httpx.Response(200, json=DISHES)
        answer = routes.get(request.url.path, 404)
        if isinstance(answer, int):
            return httpx.Response(answer, text="")
        return httpx.Response(200, json=answer)

    monkeypatch.setattr(swiggy, "transport", httpx.MockTransport(handler))
    cache.clear()
    yield routes, calls
    cache.clear()


def test_member_lists_popular_restaurants_nearby(member, swiggy_api):
    response = client_for(member).get("/api/restaurants", OFFICE)

    assert response.status_code == 200
    restaurants = response.json()["restaurants"]
    assert [r["name"] for r in restaurants] == ["Paragon", "Kayees"]
    assert restaurants[0]["delivery_time"] == "25-30 mins"
    assert restaurants[0]["image_url"].endswith("/paragon.jpg")


def test_member_searches_restaurants(member, swiggy_api):
    _, calls = swiggy_api

    response = client_for(member).get("/api/restaurants", {**OFFICE, "q": "paragon"})

    assert response.status_code == 200
    assert calls == ["/dapi/restaurants/search/v3"]


def test_menu_has_restaurant_details_and_prices_in_paise(member, swiggy_api):
    response = client_for(member).get("/api/restaurants/263840/menu", OFFICE)

    assert response.status_code == 200
    menu = response.json()
    assert menu["restaurant"]["name"] == "Paragon"
    assert menu["restaurant"]["address"] == "Kaloor, Kochi"
    items = menu["categories"][0]["items"]
    assert [(i["name"], i["price_paise"], i["veg"], i["in_stock"]) for i in items] == [
        ("Chicken Biryani", 32000, False, True),
        ("Veg Biryani", 22000, True, False),
    ]


def test_menus_are_cached_for_the_team(member, host, swiggy_api):
    _, calls = swiggy_api

    client_for(member).get("/api/restaurants/263840/menu", OFFICE)
    client_for(host).get("/api/restaurants/263840/menu", OFFICE)

    assert calls.count("/dapi/menu/pl") == 1


def test_unknown_restaurant_is_not_found(member, swiggy_api):
    routes, _ = swiggy_api
    routes["/dapi/menu/pl"] = {"data": {"cards": []}}
    routes["/mapi/menu/pl"] = {"data": {"cards": []}}

    assert (
        client_for(member).get("/api/restaurants/999/menu", OFFICE).status_code == 404
    )


def test_member_searches_dishes(member, swiggy_api):
    response = client_for(member).get("/api/dishes", {**OFFICE, "q": "biryani"})

    assert response.status_code == 200
    [dish] = response.json()["dishes"]
    assert dish["price_paise"] == 32000
    assert dish["restaurant"]["id"] == "263840"
    assert dish["in_stock"] is True


def test_dish_search_says_when_a_dish_is_out_of_stock(member, swiggy_api, monkeypatch):
    sold_out = {
        "@type": f"{FOOD}.DishGroup",
        "restaurant": {"info": RESTAURANT_INFO},
        "dishes": [
            {"info": {"id": "11", "name": "Biryani", "price": 32000, "inStock": 0}}
        ],
    }
    monkeypatch.setitem(DISHES["data"]["cards"][0]["card"], "card", sold_out)

    response = client_for(member).get("/api/dishes", {**OFFICE, "q": "biryani"})

    assert response.json()["dishes"][0]["in_stock"] is False


def test_swiggy_failing_is_a_bad_gateway(member, swiggy_api):
    routes, _ = swiggy_api
    routes["/dapi/restaurants/list/v5"] = 503

    response = client_for(member).get("/api/restaurants", OFFICE)

    assert response.status_code == 502


def test_location_is_required(member, swiggy_api):
    assert client_for(member).get("/api/restaurants").status_code == 400
    assert (
        client_for(member).get("/api/restaurants", {"lat": "x", "lng": "1"}).status_code
        == 400
    )


def test_dish_search_needs_two_letters(member, swiggy_api):
    assert (
        client_for(member).get("/api/dishes", {**OFFICE, "q": "b"}).status_code == 400
    )


def test_people_without_a_company_cannot_browse(db, swiggy_api):
    loner = make_user(None, "Lone")

    assert client_for(loner).get("/api/restaurants", OFFICE).status_code == 403
