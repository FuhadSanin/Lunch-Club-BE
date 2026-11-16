"""Turning what people say into what the API takes, and the API's answers into short
summaries an assistant can read."""

import re
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from mcp.server.mcpserver.exceptions import ToolError

UUID_IN_TEXT = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
IN_DURATION = re.compile(
    r"in\s+(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hr|hrs|hours?)", re.IGNORECASE
)
CLOCK = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE)


def parse_closing_time(text: str, now: datetime) -> datetime:
    """When picks close, from "12:30", "12:30 pm", "in 90 minutes" or an ISO time.

    A clock time that has already passed today means tomorrow. `now` carries the time
    zone the person is in.
    """
    said = text.strip()
    if m := IN_DURATION.fullmatch(said):
        n = float(m.group(1))
        unit = m.group(2).lower()
        return now + (
            timedelta(hours=n) if unit.startswith("h") else timedelta(minutes=n)
        )
    if m := CLOCK.fullmatch(said):
        hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if meridiem:
            if not 1 <= hour <= 12:
                raise ToolError(f"I couldn't read '{text}' as a time.")
            hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
        if hour > 23 or minute > 59:
            raise ToolError(f"I couldn't read '{text}' as a time.")
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return at if at > now + timedelta(minutes=2) else at + timedelta(days=1)
    try:
        at = datetime.fromisoformat(said)
    except ValueError:
        raise ToolError(
            f"I couldn't read '{text}' as a time. "
            "Try '12:30', '1:15 pm' or 'in 90 minutes'."
        ) from None
    return at if at.tzinfo else at.replace(tzinfo=now.tzinfo)


def lunch_id_from(text: str) -> str:
    """The lunch id in a bare id or a shared link like https://…/#/lunch/<id>."""
    found = UUID_IN_TEXT.search(text or "")
    if not found:
        raise ToolError(
            "I couldn't find a food party id in that. Use the food party's link or id."
        )
    return str(UUID(found.group(0)))


def address_of(board: dict[str, Any]) -> dict[str, Any]:
    """Where a lunch delivers: {address, lat, lng} (lat and lng may be None)."""
    found = board.get("address")
    if isinstance(found, dict):
        return found
    return {  # an older API put these on the lunch itself
        "address": board.get("delivery_address", ""),
        "lat": board.get("delivery_lat"),
        "lng": board.get("delivery_lng"),
    }


def rupees(paise: int | None) -> float | None:
    return None if paise is None else round(paise / 100, 2)


def local_time(iso: str, tz: ZoneInfo) -> str:
    when = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz)
    return when.strftime("%-I:%M %p").lower() + (
        "" if when.date() == datetime.now(tz).date() else when.strftime(" on %a %-d %b")
    )


def summarize_lunch(board: dict[str, Any], tz: ZoneInfo, link: str) -> dict[str, Any]:
    """A board in a form that is quick to read: who picked what, and where it stands."""
    people: dict[str, dict[str, Any]] = {}
    restaurants: dict[str, dict[str, int]] = {}
    for item in board.get("items", []):
        who = people.setdefault(
            item["user"]["id"],
            {"person": item["user"]["name"], "dishes": [], "total": 0.0},
        )
        line_paise = item["quantity"] * item["price_paise"]
        who["dishes"].append(
            {
                "item_id": item["id"],
                "dish": item["dish_name"],
                "restaurant": item["restaurant_name"],
                "quantity": item["quantity"],
                "note": item.get("note", ""),
            }
        )
        who["total"] = round(who["total"] + line_paise / 100, 2)
        r = restaurants.setdefault(item["restaurant_name"], {})
        r[item["dish_name"]] = r.get(item["dish_name"], 0) + item["quantity"]
    done = {p["id"] for p in board.get("completed", [])}
    for pid, who in people.items():
        who["finished"] = pid in done
    return {
        "lunch_id": board["id"],
        "name": board.get("name") or "",
        "host": board["host"]["name"],
        "status": board["status"],
        "picks_close": local_time(board["cutoff_at"], tz),
        "delivery_address": address_of(board)["address"],
        "share_link": link,
        "link_open_to_new_people": board.get("joining_open", True),
        "picks": list(people.values()),
        "skipping": [p["name"] for p in board.get("skipped", [])],
        "by_restaurant": {
            name: [{"dish": d, "quantity": q} for d, q in dishes.items()]
            for name, dishes in restaurants.items()
        },
        "items_total": rupees(board.get("total_paise", 0)),
        "orders": [
            {
                "order_id": o["id"],
                "restaurants": o["restaurants"],
                "status": o["status"],
                "total": rupees(o["total_paise"]),
                "order_number": o.get("platform_order_id")
                or o.get("swiggy_order_id")
                or "",
                "note": o.get("last_error", ""),
            }
            for o in board.get("orders", [])
        ],
    }
