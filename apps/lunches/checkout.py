"""What the host needs to check out each restaurant in the Swiggy app by hand."""

from collections.abc import Iterable
from urllib.parse import quote

from apps.lunches.models import OrderItem


def swiggy_url(restaurant_name: str, restaurant_id: str) -> str:
    """The restaurant's Swiggy page, or a Swiggy search for it without an id."""
    if restaurant_id:
        return f"https://www.swiggy.com/menu/{restaurant_id}"
    return f"https://www.swiggy.com/search?query={quote(restaurant_name)}"


def checkout_carts(items: Iterable[OrderItem]) -> list[dict]:
    """One cart per restaurant: its link, each dish with its total quantity and who
    wants it, and a cooking note with one "Name - food" line per person."""
    by_restaurant: dict[str, list[OrderItem]] = {}
    for item in items:
        by_restaurant.setdefault(item.restaurant_name, []).append(item)
    return [_cart(name, picks) for name, picks in sorted(by_restaurant.items())]


def _cart(name: str, picks: list[OrderItem]) -> dict:
    restaurant_id = next((p.restaurant_id for p in picks if p.restaurant_id), "")
    dishes: dict[str, dict] = {}
    foods: dict[str, list[str]] = {}
    notes: dict[str, str] = {}
    for pick in picks:
        person = pick.user.name or pick.user.email
        dish = dishes.setdefault(
            pick.dish_name, {"name": pick.dish_name, "quantity": 0, "people": []}
        )
        dish["quantity"] += pick.quantity
        dish["people"].append(person)
        food = (
            pick.dish_name
            if pick.quantity == 1
            else f"{pick.quantity}× {pick.dish_name}"
        )
        foods.setdefault(person, []).append(food)
        if pick.note:
            notes[person] = pick.note
    note = "\n".join(
        f"{person} - {', '.join(food)}"
        + (f" · Note: {notes[person]}" if person in notes else "")
        for person, food in sorted(foods.items())
    )
    return {
        "restaurant_name": name,
        "restaurant_id": restaurant_id,
        "swiggy_url": swiggy_url(name, restaurant_id),
        "dishes": list(dishes.values()),
        "cooking_note": note,
        "total_paise": sum(p.total_paise for p in picks),
    }
