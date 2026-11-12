"""Place suggestions while someone types an address, and where a chosen place is.

Swiggy's own place search isn't open to the web (its address endpoints answer 404
there), so this uses OpenStreetMap: Photon, made for suggesting as you type, and
Nominatim to describe one place. Both are free and keyless; they ask for a descriptive
User-Agent and light use, so answers are cached for a day.

A suggestion's `place_id` is OpenStreetMap's own, like `W731817480` (a way) or `N123`.
"""

import re
from typing import Any

import httpx
from django.conf import settings
from django.core.cache import cache

# Swiggy delivers in India; keeping suggestions there keeps them relevant.
INDIA_BBOX = "68,6,98,36"
PLACE_ID = re.compile(r"^[NWR]\d{1,15}$")
MAX_SUGGESTIONS = 8

# Tests swap this for an httpx.MockTransport.
transport: httpx.BaseTransport | None = None


class PlacesError(Exception):
    """The place service didn't answer, or answered with something we can't read."""


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": settings.PLACES_USER_AGENT, "Accept-Language": "en"},
        timeout=settings.SWIGGY_TIMEOUT_SECONDS,
        transport=transport,
    )


def _get(url: str, params: dict) -> Any:
    try:
        with _client() as client:
            response = client.get(url, params=params)
    except httpx.HTTPError as error:
        raise PlacesError(f"Couldn't reach the place search: {error}") from error
    if response.status_code >= 400:
        raise PlacesError(f"The place search returned HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError as error:
        raise PlacesError(
            "The place search returned something other than JSON."
        ) from error


def _parts(props: dict) -> list[str]:
    street = " ".join(
        str(p) for p in (props.get("housenumber"), props.get("street")) if p
    )
    seen: list[str] = []
    for part in (
        props.get("name"),
        street,
        props.get("district") or props.get("locality"),
        props.get("city") or props.get("county"),
        props.get("state"),
        props.get("postcode"),
    ):
        if part and part not in seen:
            seen.append(str(part))
    return seen


def _suggestion(feature: dict) -> dict | None:
    props = feature.get("properties") or {}
    try:
        lng, lat = feature["geometry"]["coordinates"][:2]
        place_id = f"{props['osm_type']}{props['osm_id']}"
    except (KeyError, TypeError, ValueError):
        return None
    parts = _parts(props)
    if not parts or not PLACE_ID.match(place_id):
        return None
    return {
        "place_id": place_id,
        "description": ", ".join(parts),
        "main_text": parts[0],
        "secondary_text": ", ".join(parts[1:]),
        "lat": float(lat),
        "lng": float(lng),
    }


def suggest(
    query: str, lat: float | None = None, lng: float | None = None
) -> list[dict]:
    """Up to 8 places matching what was typed, best first."""
    key = f"places:suggest:{query.casefold()}:{round(lat or 0, 1)}:{round(lng or 0, 1)}"
    found = cache.get(key)
    if found is None:
        params: dict[str, Any] = {
            "q": query,
            "limit": MAX_SUGGESTIONS + 4,  # some results are dropped below
            "lang": "en",
            "bbox": INDIA_BBOX,
        }
        if lat is not None and lng is not None:
            params |= {"lat": lat, "lon": lng}
        data = _get(settings.PLACES_SUGGEST_URL, params)
        found = []
        for feature in data.get("features", []) if isinstance(data, dict) else []:
            item = _suggestion(feature)
            if item and all(item["place_id"] != f["place_id"] for f in found):
                found.append(item)
        found = found[:MAX_SUGGESTIONS]
        for item in found:  # so choosing one needs no second call
            cache.set(f"places:detail:{item['place_id']}", _detail(item), _ttl())
        cache.set(key, found, _ttl())
    return [
        {k: v for k, v in item.items() if k not in ("lat", "lng")} for item in found
    ]


def _ttl() -> int:
    return settings.PLACES_CACHE_SECONDS


def _detail(item: dict) -> dict:
    return {
        "place_id": item["place_id"],
        "address": item["description"],
        "lat": item["lat"],
        "lng": item["lng"],
    }


def detail(place_id: str) -> dict | None:
    """The address and coordinates of a suggested place, or None if it isn't one."""
    if not PLACE_ID.match(place_id):
        return None
    key = f"places:detail:{place_id}"
    found = cache.get(key)
    if found is None:
        data = _get(
            settings.PLACES_LOOKUP_URL,
            {"osm_ids": place_id, "format": "jsonv2", "addressdetails": 0},
        )
        if not isinstance(data, list) or not data:
            return None
        try:
            place = data[0]
            found = {
                "place_id": place_id,
                "address": place["display_name"],
                "lat": float(place["lat"]),
                "lng": float(place["lon"]),
            }
        except (KeyError, TypeError, ValueError) as error:
            raise PlacesError(
                "The place search answered in a form we can't read."
            ) from error
        cache.set(key, found, _ttl())
    return found
