"""What people browse while picking: Swiggy restaurants, menus and dishes nearby.

Answers are cached briefly per location, since the whole team opens the same menus
around the same time and Swiggy is slow.
"""

from django.conf import settings
from django.core.cache import cache

from apps.catalog import swiggy


def _cached(key: str, fetch):
    value = cache.get(key)
    if value is None:
        value = fetch()
        cache.set(key, value, settings.SWIGGY_CACHE_SECONDS)
    return value


def _place(lat: float, lng: float) -> str:
    # About 100 m, close enough that Swiggy shows the same restaurants.
    return f"{lat:.3f},{lng:.3f}"


def restaurants(lat: float, lng: float, query: str = "") -> list[dict]:
    """Restaurants matching `query`, or popular ones nearby without one."""
    if query:
        return _cached(
            f"swiggy:search:{_place(lat, lng)}:{query.casefold()}",
            lambda: swiggy.search_restaurants(query, lat, lng),
        )
    return _cached(
        f"swiggy:nearby:{_place(lat, lng)}", lambda: swiggy.nearby_restaurants(lat, lng)
    )


def dishes(lat: float, lng: float, query: str) -> list[dict]:
    return _cached(
        f"swiggy:dishes:{_place(lat, lng)}:{query.casefold()}",
        lambda: swiggy.search_dishes(query, lat, lng),
    )


def menu(restaurant_id: str, lat: float, lng: float) -> dict | None:
    key = f"swiggy:menu:{_place(lat, lng)}:{restaurant_id}"
    found = cache.get(key)
    if found is None:
        found = swiggy.menu(restaurant_id, lat, lng)
        if found is not None:
            cache.set(key, found, settings.SWIGGY_CACHE_SECONDS)
    return found
