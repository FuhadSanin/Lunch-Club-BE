"""Restaurants, menus and dishes from Swiggy's public web endpoints.

These are the calls swiggy.com makes in the browser, so no login is needed. They are
unofficial and change without notice; parsing walks the JSON recursively so it survives
most layout shuffles. Prices come back from Swiggy in paise and are kept that way.
"""

import contextlib
from collections.abc import Iterator
from typing import Any

import httpx
from django.conf import settings

BASE = "https://www.swiggy.com/dapi"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.swiggy.com/",
}
IMAGE_URL = "https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy,f_auto,q_auto,w_660/{}"

# Tests swap this for an httpx.MockTransport.
transport: httpx.BaseTransport | None = None


class SwiggyError(Exception):
    """Swiggy didn't answer, or answered with something we can't read."""


def _client() -> httpx.Client:
    return httpx.Client(
        headers=HEADERS,
        timeout=settings.SWIGGY_TIMEOUT_SECONDS,
        follow_redirects=True,
        transport=transport,
    )


def _get(client: httpx.Client, url: str, **kwargs) -> Any:
    try:
        response = client.get(url, **kwargs)
    except httpx.HTTPError as error:
        raise SwiggyError(f"Couldn't reach Swiggy: {error}") from error
    if response.status_code >= 400:
        raise SwiggyError(f"Swiggy returned HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError as error:
        raise SwiggyError("Swiggy returned something other than JSON.") from error


def _walk(obj: Any) -> Iterator[dict]:
    """Yield every dict nested anywhere inside obj."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def _image(image_id: str | None) -> str | None:
    return IMAGE_URL.format(image_id) if image_id else None


def _price_paise(info: dict) -> int | None:
    price = info.get("price") or info.get("defaultPrice") or info.get("finalPrice")
    return round(price) if isinstance(price, int | float) else None


def _restaurant(info: dict) -> dict:
    sla = info.get("sla") or {}
    return {
        "id": str(info.get("id")),
        "name": info.get("name"),
        "area": info.get("areaName") or info.get("locality"),
        "cuisines": info.get("cuisines") or [],
        "rating": info.get("avgRating") or info.get("avgRatingString"),
        "cost_for_two": info.get("costForTwoMessage") or info.get("costForTwo"),
        "delivery_time": sla.get("slaString")
        or (f"{sla['deliveryTime']} mins" if sla.get("deliveryTime") else None),
        "distance_km": sla.get("lastMileTravel"),
        "open": (info.get("availability") or {}).get("opened", info.get("isOpen")),
        "image_url": _image(info.get("cloudinaryImageId")),
    }


def _restaurants(data: Any) -> list[dict]:
    seen, found = set(), []
    for node in _walk(data):
        info = node.get("info")
        if (
            isinstance(info, dict)
            and info.get("id")
            and info.get("name")
            and "cuisines" in info
        ):
            restaurant_id = str(info["id"])
            if restaurant_id not in seen:
                seen.add(restaurant_id)
                found.append(_restaurant(info))
    return found


def nearby_restaurants(lat: float, lng: float) -> list[dict]:
    """Popular restaurants delivering to a location."""
    with _client() as client:
        data = _get(
            client,
            f"{BASE}/restaurants/list/v5",
            params={
                "lat": lat,
                "lng": lng,
                "is-seo-homepage-enabled": "true",
                "page_type": "DESKTOP_WEB_LISTING",
            },
        )
    return _restaurants(data)


def search_restaurants(query: str, lat: float, lng: float) -> list[dict]:
    """Restaurants matching a name or dish near a location."""
    with _client() as client:
        data = _get(
            client,
            f"{BASE}/restaurants/search/v3",
            params={
                "lat": lat,
                "lng": lng,
                "str": query,
                "submitAction": "ENTER",
                "selectedPLTab": "RESTAURANT",
            },
        )
    return _restaurants(data)


def search_dishes(query: str, lat: float, lng: float, limit: int = 24) -> list[dict]:
    """Dishes matching a name, each with the restaurant that serves it."""
    with _client() as client:
        data = _get(
            client,
            f"{BASE}/restaurants/search/v3",
            params={
                "lat": lat,
                "lng": lng,
                "str": query,
                "submitAction": "ENTER",
                "selectedPLTab": "DISH",
            },
        )
    dishes, seen = [], set()
    for group in _walk(data):
        if not str(group.get("@type", "")).endswith("DishGroup"):
            continue
        restaurant_info = (group.get("restaurant") or {}).get("info") or {}
        restaurant_id = str(restaurant_info.get("id") or "")
        if not restaurant_id.isdigit():
            continue
        restaurant = _restaurant(restaurant_info)
        for card in group.get("dishes") or []:
            info = (card or {}).get("info") or {}
            dish_id, name = (
                str(info.get("id") or ""),
                str(info.get("name") or "").strip(),
            )
            if not dish_id or not name or (restaurant_id, dish_id) in seen:
                continue
            seen.add((restaurant_id, dish_id))
            dishes.append(
                {
                    "id": dish_id,
                    "name": name,
                    "price_paise": _price_paise(info),
                    "veg": info.get("isVeg") == 1,
                    "in_stock": info.get("inStock", 1) == 1,
                    "image_url": _image(info.get("imageId")),
                    "restaurant": restaurant,
                }
            )
            if len(dishes) >= limit:
                return dishes
    return dishes


def _menu_items(data: Any) -> list[dict]:
    categories: dict[str, list[dict]] = {}
    seen = set()
    for node in _walk(data):
        cards = node.get("itemCards")
        if not (isinstance(cards, list) and node.get("title")):
            continue
        items = categories.setdefault(node["title"], [])
        for card in cards:
            info = ((card or {}).get("card") or {}).get("info") or {}
            if not info.get("name") or info.get("id") in seen:
                continue
            seen.add(info.get("id"))
            rating = ((info.get("ratings") or {}).get("aggregatedRating") or {}).get(
                "rating"
            )
            items.append(
                {
                    "id": str(info.get("id") or ""),
                    "name": info["name"],
                    "price_paise": _price_paise(info),
                    "veg": info.get("isVeg") == 1
                    or (info.get("itemAttribute") or {}).get("vegClassifier") == "VEG",
                    "description": (info.get("description") or "").strip() or None,
                    "rating": rating,
                    "in_stock": info.get("inStock", 1) == 1,
                    "image_url": _image(info.get("imageId")),
                }
            )
    return [
        {"name": name, "items": items} for name, items in categories.items() if items
    ]


def _menu_restaurant(data: Any, restaurant_id: str) -> dict | None:
    """The restaurant card on a menu page, plus its address and offers."""
    details = None
    for node in _walk(data):
        if str(node.get("@type", "")).endswith("food.v2.Restaurant") and isinstance(
            node.get("info"), dict
        ):
            info = node["info"]
            details = {
                **_restaurant(info),
                "id": str(info.get("id") or restaurant_id),
                "city": info.get("city"),
                "ratings_count": info.get("totalRatingsString"),
                "closes_at": (info.get("availability") or {}).get("nextCloseTime"),
                "veg_only": bool(info.get("veg")),
                "delivery_fee": (info.get("feeDetails") or {}).get("message"),
            }
            break
    if details is None:
        return None
    offers = []
    for node in _walk(data):
        if str(node.get("@type", "")).endswith("RestaurantAddress"):
            details["address"] = node.get("completeAddress")
        info = node.get("info") if isinstance(node.get("info"), dict) else None
        if (
            info
            and info.get("header")
            and ("couponCode" in info or "offerIds" in info or "offerTag" in info)
        ):
            offer = " · ".join(
                part
                for part in [
                    info.get("header"),
                    info.get("couponCode") or info.get("description"),
                ]
                if part
            )
            if offer not in offers:
                offers.append(offer)
    details["offers"] = offers[:6]
    return details


def menu(restaurant_id: str, lat: float, lng: float) -> dict | None:
    """One restaurant's details and full menu, or None if Swiggy doesn't know it here.

    Swiggy often refuses menu calls without its session cookies, so this opens the home
    page first, and falls back from the desktop to the mobile endpoint.
    """
    params = {
        "page-type": "REGULAR_MENU",
        "complete-menu": "true",
        "lat": lat,
        "lng": lng,
        "restaurantId": restaurant_id,
        "catalog_qa": "undefined",
        "submitAction": "ENTER",
    }
    headers = {"Referer": f"https://www.swiggy.com/restaurants/{restaurant_id}"}
    errors, without_items = [], None
    with _client() as client:
        with contextlib.suppress(httpx.HTTPError):
            client.get("https://www.swiggy.com/", headers={"Accept": "text/html"})
        for base in (BASE, "https://www.swiggy.com/mapi"):
            try:
                data = _get(client, f"{base}/menu/pl", params=params, headers=headers)
            except SwiggyError as error:
                errors.append(str(error))
                continue
            restaurant = _menu_restaurant(data, restaurant_id)
            if restaurant is None:
                continue
            categories = _menu_items(data)
            result = {"restaurant": restaurant, "categories": categories}
            if categories:
                return result
            # Closed or not delivering here: details but no dishes. Try the other one.
            without_items = without_items or result
    if without_items is not None:
        return without_items
    if errors and len(errors) == 2:
        raise SwiggyError(" ".join(errors))
    return None
