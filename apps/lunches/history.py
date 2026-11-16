"""The receipt of one lunch, for the host who ran it: what each person ordered, the
restaurants, and how much each person owes the host."""

from typing import Any

from apps.lunches.models import OrderItem, OrderSession


def _details(lunch: OrderSession) -> tuple[dict[str, dict], dict[str, dict]]:
    """The restaurants the host offered, by Swiggy id and by name."""
    by_id: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for found in lunch.offered_restaurants or []:
        if isinstance(found, dict):
            by_id[str(found.get("id", ""))] = found
            by_name[str(found.get("name", "")).strip().lower()] = found
    return by_id, by_name


def _restaurant_card(found: dict | None) -> dict[str, Any]:
    found = found or {}
    return {
        "area": found.get("area"),
        "cuisines": found.get("cuisines") or [],
        "rating": found.get("rating"),
        "eta": found.get("eta"),
        "distance_km": found.get("distance_km"),
        "photo": found.get("photo"),
    }


def _owed(item: OrderItem) -> int:
    return item.quantity * item.price_paise


def lunch_receipt(lunch: OrderSession) -> dict[str, Any]:
    """Expects `lunches.queries.with_board`: items and orders already loaded."""
    items = list(lunch.items.all())
    by_id, by_name = _details(lunch)
    order_of = {}
    for placed in lunch.orders.all():
        for item in placed.items.all():
            order_of[item.id] = placed

    restaurants: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        key = (item.restaurant_id, item.restaurant_name)
        found = by_id.get(item.restaurant_id) or by_name.get(
            item.restaurant_name.strip().lower()
        )
        entry = restaurants.setdefault(
            key,
            {
                "name": item.restaurant_name,
                "restaurant_id": item.restaurant_id,
                **_restaurant_card(found),
                "order": None,
                "dishes": {},
                "total_paise": 0,
            },
        )
        order = order_of.get(item.id)
        if order is not None and entry["order"] is None:
            entry["order"] = {
                "status": order.status,
                "platform_order_id": order.platform_order_id,
                "placed_at": order.placed_at,
            }
        dish = entry["dishes"].setdefault(
            (item.dish_name, item.price_paise),
            {
                "dish_name": item.dish_name,
                "price_paise": item.price_paise,
                "quantity": 0,
                "people": [],
            },
        )
        dish["quantity"] += item.quantity
        if item.user.name not in dish["people"]:
            dish["people"].append(item.user.name)
        entry["total_paise"] += _owed(item)

    people: dict[Any, dict[str, Any]] = {}
    for item in items:
        who = people.setdefault(
            item.user_id,
            {
                "user": {"id": str(item.user_id), "name": item.user.name},
                "owes_paise": 0,
                "items": [],
            },
        )
        who["owes_paise"] += _owed(item)
        who["items"].append(
            {
                "restaurant_name": item.restaurant_name,
                "dish_name": item.dish_name,
                "quantity": item.quantity,
                "price_paise": item.price_paise,
                "note": item.note,
            }
        )

    return {
        "id": str(lunch.id),
        "name": lunch.name,
        "host": {"id": str(lunch.host_id), "name": lunch.host.name},
        "cutoff_at": lunch.cutoff_at,
        "status": lunch.status,
        "closed_at": lunch.closed_at,
        "address": lunch.address.address,
        "total_paise": sum(_owed(i) for i in items),
        "restaurants": [
            {**r, "dishes": list(r["dishes"].values())} for r in restaurants.values()
        ],
        "people": sorted(people.values(), key=lambda p: p["user"]["name"].lower()),
    }
