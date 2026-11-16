"""The tools, prompts and instructions of the Tiffmate MCP server."""

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from mcp_server import helpers
from mcp_server.api import MISSING, Api

INSTRUCTIONS = """\\
Tiffmate runs group food orders for any meal or occasion (breakfast, lunch, dinner,
snacks, food parties). Someone starts a food party (a cutoff time and a delivery
place), shares
one link, everyone picks dishes from Swiggy menus, the host locks the picks, reviews
the cart, then orders in the Swiggy app and marks it ordered. A "food party" is what the
tools call a lunch in their names (start_lunch, get_lunch, ...).

You act as the signed-in person, with their own permissions. Typical flow for a host:
1. start_lunch (ask for the closing time and the delivery place if you don't have them),
2. share_lunch, and give the person the message and link to send to their team,
3. later get_lunch to see who picked what, then lock_lunch,
4. confirm_lunch only after the person has reviewed get_lunch and said yes,
5. the person orders in the Swiggy app (automatic ordering waits for Swiggy approval),
   then you can mark_order_placed and mark_order_arrived for them.
For a guest: join_lunch with the link, find_restaurants / get_menu, pick_dish, then
finish_my_order. Never invent ids: use the ones tools return. Money is in rupees.
Ask before anything that is hard to undo: confirm_lunch and cancel_lunch need an
explicit yes from the person.
"""


class MenuItem(BaseModel):
    """One dish of a local menu."""

    name: str = Field(description="The dish's name, as the menu writes it.")
    price: float = Field(description="Price in rupees, like 120 or 89.50.")
    image: str = Field(
        default="",
        description="Optional picture of the dish: a link starting with https://.",
    )
    veg: bool | None = Field(
        default=None,
        description="True for vegetarian, false for non-veg, leave out if the "
        "menu doesn't say.",
    )


READ = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)


@dataclass
class Config:
    api_url: str = field(
        default_factory=lambda: os.environ.get(
            "LUNCHCLUB_API_URL", "http://127.0.0.1:8001"
        )
    )
    web_url: str = field(
        default_factory=lambda: os.environ.get(
            "LUNCHCLUB_WEB_URL", "https://tiffmate.vercel.app"
        )
    )
    timezone: str = field(
        default_factory=lambda: os.environ.get("LUNCHCLUB_TZ", "Asia/Kolkata")
    )
    # A token from the environment is for a program run by one person on their own
    # machine (stdio). A shared HTTP server must never use it, or everyone acts as them.
    env_token: str | None = None
    transport: httpx.AsyncBaseTransport | None = None  # tests point this at the app
    now: Any = None  # tests fix the clock

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def clock(self) -> datetime:
        return self.now() if self.now else datetime.now(self.tz)

    def link(self, lunch_id: str) -> str:
        return f"{self.web_url.rstrip('/')}/#/lunch/{lunch_id}"


def create_server(config: Config | None = None) -> MCPServer:
    cfg = config or Config()
    server = MCPServer("Tiffmate", instructions=INSTRUCTIONS)

    def api_for(ctx: Context) -> Api:
        """The API as the caller. Over HTTP that is the token on their own request, and
        only that. The environment's token is for stdio, a program one person runs on
        their own machine (there `ctx.headers` is None); a shared server falling back to
        it would let anyone who reaches it act as that person."""
        if ctx.headers is None:
            token = cfg.env_token or ""
        else:
            scheme, _, sent = ctx.headers.get("authorization", "").partition(" ")
            token = sent if scheme.lower() == "bearer" else ""
        if not token:
            raise ToolError(MISSING)
        return Api(cfg.api_url, token, cfg.transport)

    async def where(
        api: Api,
        lunch_id: str | None,
        near: str | None,
        lat: float | None,
        lng: float | None,
    ) -> tuple[float, float]:
        """Coordinates restaurants are looked up from: given, a place, or a party's."""
        if lat is not None and lng is not None:
            return lat, lng
        if near:
            found = await api.get("places", q=near)
            if not found:
                raise ToolError(f"I couldn't find '{near}'. Try search_places.")
            return found[0]["lat"], found[0]["lng"]
        if lunch_id:
            board = await api.get(f"lunches/{helpers.lunch_id_from(lunch_id)}")
            there = helpers.address_of(board)
            if there.get("lat") is not None:
                return there["lat"], there["lng"]
        raise ToolError(
            "I need somewhere to look near: pass lunch_id (of a lunch with a delivery "
            "place), a place name in `near`, or lat and lng."
        )

    async def board_of(api: Api, lunch_id: str) -> dict[str, Any]:
        return await api.get(f"lunches/{helpers.lunch_id_from(lunch_id)}")

    # ---- you ----

    @server.tool(annotations=READ)
    async def whoami(ctx: Context) -> dict[str, Any]:
        """Who you are acting as in Tiffmate, and which company or food party."""
        me = await api_for(ctx).get("me")
        org = me.get("org") or {}
        return {
            "name": me["name"],
            "email": me["email"],
            "group": org.get("name")
            or "none yet (made when you start your first food party)",
            "kind": org.get("kind"),
            "can_start_lunches": bool(me.get("public_email"))
            or any(p == "session_create" for p in me.get("permissions", [])),
        }

    # ---- local menus ----

    @server.tool(annotations=WRITE)
    async def add_local_menu(
        ctx: Context,
        restaurant_name: str,
        items: list[MenuItem],
        rating: float | None = None,
        distance_km: float | None = None,
        image: str = "",
    ) -> dict[str, Any]:
        """Save a local restaurant's menu (one that isn't on Swiggy), so you can offer
        it when you start a party. Read it from the photo or text the person gave you:
        every dish with its price. restaurant_name and at least one dish with a price
        are required; rating (0 to 5), distance_km and an https image link are
        optional. A big menu (up to 300 dishes) goes in one call. A dish with no
        fixed price (like "as per size") can't be saved: ask the person for a price,
        or leave it out and say so. Ask the person for anything you can't read. Then
        offer to start a food party with it (start_lunch with local_menu_ids)."""
        body: dict[str, Any] = {
            "name": restaurant_name,
            "image": image,
            "items": [
                {
                    "name": i.name,
                    "price_paise": round(i.price * 100),
                    "image": i.image,
                    "veg": i.veg,
                }
                for i in items
            ],
        }
        if rating is not None:
            body["rating"] = rating
        if distance_km is not None:
            body["distance_km"] = distance_km
        menu = await api_for(ctx).call("POST", "local-menus", json=body)
        return {
            "menu_id": menu["id"],
            "restaurant": menu["name"],
            "dishes": len(menu["items"]),
            "next": "It is in the Local menu page of Tiffmate. Offer to start a party "
            "with it (start_lunch with local_menu_ids).",
        }

    @server.tool(annotations=READ)
    async def list_local_menus(ctx: Context) -> list[dict[str, Any]]:
        """The local menus you keep (restaurants that aren't on Swiggy)."""
        menus = await api_for(ctx).get("local-menus")
        return [
            {
                "menu_id": m["id"],
                "restaurant": m["name"],
                "rating": m.get("rating"),
                "distance_km": m.get("distance_km"),
                "dishes": [
                    {"name": i["name"], "price": helpers.rupees(i["price_paise"])}
                    for i in m["items"]
                ],
            }
            for m in menus
        ]

    @server.tool(annotations=DESTRUCTIVE)
    async def delete_local_menu(
        ctx: Context, menu_id: str, confirm: bool = False
    ) -> str:
        """Delete one of your local menus. Needs confirm=true (ask first). Food parties
        already started keep their copy."""
        if not confirm:
            raise ToolError(
                "Not confirmed. Ask the person, then call with confirm=true."
            )
        await api_for(ctx).call("DELETE", f"local-menus/{menu_id}")
        return "Deleted."

    # ---- starting and sharing ----

    @server.tool(annotations=READ)
    async def search_places(ctx: Context, query: str) -> list[dict[str, Any]]:
        """Find places (building, area, landmark) to deliver to. Returns names with
        coordinates; start_lunch takes the best match for a place name by itself."""
        found = await api_for(ctx).get("places", q=query)
        return [
            {"name": p["description"], "lat": p.get("lat"), "lng": p.get("lng")}
            for p in found
        ]

    @server.tool(annotations=WRITE)
    async def start_lunch(
        ctx: Context,
        closes_at: str,
        delivery_place: str,
        name: str = "",
        delivery_lat: float | None = None,
        delivery_lng: float | None = None,
        local_menu_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Start a group food party and get its share link.

        closes_at: when picks close, like "12:30", "1:15 pm" or "in 90 minutes" (must be
        within the next 24 hours). delivery_place: where the food goes, like "Infopark
        Kochi" (the best match is used; its full address is returned so you can check
        it). name: optional, like "Friday team party". local_menu_ids: optional menu_ids
        from list_local_menus, to offer those local restaurants in the food party.
        """
        api = api_for(ctx)
        now = cfg.clock()
        closes = helpers.parse_closing_time(closes_at, now)
        if closes - now > timedelta(hours=24):
            raise ToolError("Picks can close at most 24 hours from now.")
        if delivery_lat is None or delivery_lng is None:
            found = await api.get("places", q=delivery_place)
            if not found:
                raise ToolError(
                    f"I couldn't find '{delivery_place}'. Try search_places."
                )
            delivery_place = found[0]["description"]
            delivery_lat, delivery_lng = found[0]["lat"], found[0]["lng"]
        body: dict[str, Any] = {
            "cutoff_at": closes.isoformat(),
            "address": {
                "address": delivery_place,
                "lat": delivery_lat,
                "lng": delivery_lng,
            },
        }
        if name.strip():
            body["name"] = name.strip()
        if local_menu_ids:
            body["local_menu_ids"] = local_menu_ids
        board = await api.call("POST", "lunches", json=body)
        return {
            "lunch_id": board["id"],
            "name": board.get("name") or "",
            "picks_close": helpers.local_time(board["cutoff_at"], cfg.tz),
            "delivering_to": helpers.address_of(board)["address"],
            "share_link": cfg.link(board["id"]),
            "next": "Share the link with the team (share_lunch writes the message).",
        }

    @server.tool(annotations=READ)
    async def share_lunch(ctx: Context, lunch_id: str) -> dict[str, str]:
        """The link to share for a party, and a message ready to paste in a chat."""
        board = await board_of(api_for(ctx), lunch_id)
        link = cfg.link(board["id"])
        title = board.get("name") or "lunch"
        closes = helpers.local_time(board["cutoff_at"], cfg.tz)
        return {
            "share_link": link,
            "message": (
                f"{board['host']['name']} started {title}. "
                f"Pick your dishes by {closes}: {link}"
            ),
        }

    @server.tool(annotations=WRITE)
    async def set_link_open(
        ctx: Context, lunch_id: str, allow_new_people: bool
    ) -> dict[str, Any]:
        """Host only: let new people in through the share link (true), or stop that
        (false). People already in stay. Picks closing also ends the link."""
        board = await api_for(ctx).call(
            "PUT",
            f"lunches/{helpers.lunch_id_from(lunch_id)}/joining",
            json={"open": allow_new_people},
        )
        return helpers.summarize_lunch(board, cfg.tz, cfg.link(board["id"]))

    @server.tool(annotations=WRITE)
    async def join_lunch(ctx: Context, lunch: str) -> dict[str, Any]:
        """Join a food party from its shared link (or id), so you can pick dishes."""
        board = await api_for(ctx).call(
            "POST", f"lunches/{helpers.lunch_id_from(lunch)}/join"
        )
        return helpers.summarize_lunch(board, cfg.tz, cfg.link(board["id"]))

    # ---- looking ----

    @server.tool(annotations=READ)
    async def list_lunches(
        ctx: Context, running_only: bool = True
    ) -> list[dict[str, Any]]:
        """Your food parties (ones you host and ones you joined), newest first."""
        lunches = await api_for(ctx).get("lunches")
        running = {"open", "locked", "confirmed", "placed"}
        return [
            {
                "lunch_id": x["id"],
                "name": x.get("name") or "",
                "host": x["host"]["name"],
                "status": x["status"],
                "picks_close": helpers.local_time(x["cutoff_at"], cfg.tz),
                "share_link": cfg.link(x["id"]),
                "joined_as_guest": bool(x.get("joined")),
            }
            for x in lunches
            if not running_only or x["status"] in running
        ]

    @server.tool(annotations=READ)
    async def get_lunch(ctx: Context, lunch_id: str) -> dict[str, Any]:
        """A party's board: status, who picked what, totals by restaurant, orders."""
        board = await board_of(api_for(ctx), lunch_id)
        return helpers.summarize_lunch(board, cfg.tz, cfg.link(board["id"]))

    # ---- choosing food ----

    @server.tool(annotations=READ)
    async def find_restaurants(
        ctx: Context,
        lunch_id: str | None = None,
        near: str | None = None,
        search: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
    ) -> list[dict[str, Any]]:
        """Swiggy restaurants near a lunch's delivery place (pass lunch_id), or near a
        place name (`near`) or coordinates. `search` narrows by name or cuisine."""
        api = api_for(ctx)
        la, lo = await where(api, lunch_id, near, lat, lng)
        found = await api.get("restaurants", lat=la, lng=lo, q=search)
        return [
            {
                "restaurant_id": r["id"],
                "name": r["name"],
                "area": r.get("area"),
                "cuisines": r.get("cuisines", []),
                "rating": r.get("rating"),
                "delivery_time": r.get("delivery_time"),
                "distance_km": r.get("distance_km"),
                "open": r.get("open"),
            }
            for r in found["restaurants"]
        ][:25]

    @server.tool(annotations=READ)
    async def get_menu(
        ctx: Context,
        restaurant_id: str,
        lunch_id: str | None = None,
        near: str | None = None,
        search: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
    ) -> dict[str, Any]:
        """A restaurant's dishes with prices (rupees). `search` filters by dish name.
        Pass lunch_id (or near / lat+lng) so the menu is the one for that place."""
        api = api_for(ctx)
        la, lo = await where(api, lunch_id, near, lat, lng)
        menu = await api.get(f"restaurants/{restaurant_id}/menu", lat=la, lng=lo)
        dishes = _dishes(menu, search)
        return {
            "restaurant": menu["restaurant"]["name"],
            "restaurant_id": restaurant_id,
            "dishes": dishes[:40],
            "more": max(0, len(dishes) - 40),
        }

    @server.tool(annotations=READ)
    async def search_dishes(
        ctx: Context,
        query: str,
        lunch_id: str | None = None,
        near: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
    ) -> list[dict[str, Any]]:
        """Find a dish by name across restaurants near the delivery place."""
        api = api_for(ctx)
        la, lo = await where(api, lunch_id, near, lat, lng)
        found = await api.get("dishes", lat=la, lng=lo, q=query)
        return [
            {
                "dish_id": d["id"],
                "dish": d["name"],
                "price": helpers.rupees(d.get("price_paise")),
                "veg": d.get("veg"),
                "in_stock": d.get("in_stock"),
                "restaurant": d["restaurant"]["name"],
                "restaurant_id": d["restaurant"]["id"],
            }
            for d in found["dishes"]
        ][:25]

    @server.tool(annotations=WRITE)
    async def pick_dish(
        ctx: Context,
        lunch_id: str,
        restaurant_id: str,
        dish: str,
        quantity: int = 1,
        note: str = "",
    ) -> dict[str, Any]:
        """Add a dish to your picks. `dish` is its dish_id or its name (a unique match);
        the price comes from the live menu. note: e.g. "less spicy" (200 characters)."""
        api = api_for(ctx)
        lunch_id = helpers.lunch_id_from(lunch_id)
        la, lo = await where(api, lunch_id, None, None, None)
        menu = await api.get(f"restaurants/{restaurant_id}/menu", lat=la, lng=lo)
        chosen = _choose(_dishes(menu, None), dish)
        if not chosen["in_stock"]:
            raise ToolError(f"{chosen['dish']} is out of stock right now.")
        if chosen["price"] is None:
            raise ToolError(
                f"{chosen['dish']} has no price on Swiggy, so I can't add it."
            )
        item = await api.call(
            "POST",
            f"lunches/{lunch_id}/items",
            json={
                "restaurant_name": menu["restaurant"]["name"],
                "restaurant_id": restaurant_id,
                "dish_name": chosen["dish"],
                "quantity": quantity,
                "price_paise": round(chosen["price"] * 100),
                "note": note,
            },
        )
        return {
            "item_id": item["id"],
            "added": f"{quantity} x {item['dish_name']} from {item['restaurant_name']}",
            "price_each": helpers.rupees(item["price_paise"]),
            "next": "Add more, or finish_my_order when you're done.",
        }

    @server.tool(annotations=DESTRUCTIVE)
    async def remove_pick(ctx: Context, lunch_id: str, item_id: str) -> str:
        """Remove one of your picks (item_id from get_lunch or pick_dish)."""
        lid = helpers.lunch_id_from(lunch_id)
        await api_for(ctx).call("DELETE", f"lunches/{lid}/items/{item_id}")
        return "Removed."

    @server.tool(annotations=WRITE)
    async def finish_my_order(ctx: Context, lunch_id: str) -> dict[str, Any]:
        """Mark your picks as final (the host can reopen them if you need to change)."""
        board = await api_for(ctx).call(
            "PUT", f"lunches/{helpers.lunch_id_from(lunch_id)}/complete"
        )
        return helpers.summarize_lunch(board, cfg.tz, cfg.link(board["id"]))

    @server.tool(annotations=WRITE)
    async def skip_lunch(ctx: Context, lunch_id: str) -> str:
        """Sit this party out: your picks are dropped. It's final: only the host can
        let you back in (the web app has a Reopen button)."""
        lid = helpers.lunch_id_from(lunch_id)
        await api_for(ctx).call("PUT", f"lunches/{lid}/skip")
        return "You're skipping this food party."

    # ---- running the lunch (host) ----

    @server.tool(annotations=WRITE)
    async def lock_lunch(ctx: Context, lunch_id: str) -> dict[str, Any]:
        """Close picks now, before the cutoff. The cart is then ready to review."""
        board = await api_for(ctx).call(
            "POST", f"lunches/{helpers.lunch_id_from(lunch_id)}/lock"
        )
        return helpers.summarize_lunch(board, cfg.tz, cfg.link(board["id"]))

    @server.tool(annotations=WRITE)
    async def unlock_lunch(ctx: Context, lunch_id: str) -> dict[str, Any]:
        """Reopen locked picks (before the order goes out)."""
        board = await api_for(ctx).call(
            "POST", f"lunches/{helpers.lunch_id_from(lunch_id)}/unlock"
        )
        return helpers.summarize_lunch(board, cfg.tz, cfg.link(board["id"]))

    @server.tool(annotations=DESTRUCTIVE)
    async def confirm_lunch(
        ctx: Context, lunch_id: str, confirm: bool = False
    ) -> dict[str, Any]:
        """Approve the reviewed cart so the Swiggy orders are prepared (one per
        restaurant; call get_lunch a moment later to see them). Only call with
        confirm=true after the person has looked at get_lunch and said yes."""
        if not confirm:
            raise ToolError(
                "Not confirmed. Show the person get_lunch (who picked what, total), "
                "ask if they want to go ahead, then call again with confirm=true."
            )
        board = await api_for(ctx).call(
            "POST", f"lunches/{helpers.lunch_id_from(lunch_id)}/confirm"
        )
        return helpers.summarize_lunch(board, cfg.tz, cfg.link(board["id"]))

    @server.tool(annotations=DESTRUCTIVE)
    async def cancel_lunch(
        ctx: Context, lunch_id: str, confirm: bool = False
    ) -> dict[str, Any]:
        """Call off a party that isn't ordered yet. Needs confirm=true (ask first)."""
        if not confirm:
            raise ToolError(
                "Not confirmed. Ask the person, then call with confirm=true."
            )
        board = await api_for(ctx).call(
            "POST", f"lunches/{helpers.lunch_id_from(lunch_id)}/cancel"
        )
        return helpers.summarize_lunch(board, cfg.tz, cfg.link(board["id"]))

    @server.tool(annotations=WRITE)
    async def mark_order_placed(
        ctx: Context, order_id: str, order_number: str = ""
    ) -> str:
        """After the host has ordered a restaurant in the Swiggy app: record it (the
        order number from the app is optional). order_id comes from get_lunch."""
        await api_for(ctx).call(
            "POST",
            f"orders/{order_id}/placed",
            # the API's field was renamed from swiggy_order_id; either is understood
            json={"platform_order_id": order_number, "swiggy_order_id": order_number},
        )
        return "Marked as ordered. The next restaurant starts if one is waiting."

    @server.tool(annotations=WRITE)
    async def mark_order_arrived(ctx: Context, order_id: str) -> str:
        """The food from a Swiggy order has arrived."""
        await api_for(ctx).call("POST", f"orders/{order_id}/delivered")
        return "Marked as arrived."

    # ---- what you were told ----

    @server.tool(annotations=READ)
    async def my_notifications(ctx: Context) -> dict[str, Any]:
        """What Tiffmate told you lately (food party started, ordered, arrived)."""
        got = await api_for(ctx).get("notifications")
        return {
            "unread": got["unread"],
            "items": [
                {
                    "title": n["title"],
                    "body": n["body"],
                    "read": n["read"],
                    "at": helpers.local_time(n["created_at"], cfg.tz),
                }
                for n in got["items"][:15]
            ],
        }

    @server.tool(annotations=WRITE)
    async def mark_notifications_read(ctx: Context) -> str:
        """Mark all your notifications as read."""
        await api_for(ctx).call("POST", "notifications/read", json={})
        return "All read."

    # ---- guided starts ----

    @server.prompt()
    def start_team_lunch(closes_at: str = "", place: str = "", name: str = "") -> str:
        """Start a food party and get the link to share."""
        asked = [
            f"Picks close at: {closes_at or '(ask me)'}",
            f"Deliver to: {place or '(ask me)'}",
            f"Name: {name or '(optional, suggest one)'}",
        ]
        return (
            "Start a Tiffmate group food party for me.\n"
            + "\n".join(asked)
            + "\nIf anything is missing, ask me first. Then use start_lunch, then "
            "share_lunch and show me the message to send my team."
        )

    @server.prompt()
    def pick_my_lunch(lunch_link: str, craving: str = "") -> str:
        """Join a food party from its link and choose what to eat."""
        return (
            f"Join this Tiffmate food party: {lunch_link}\n"
            f"I feel like: {craving or '(ask me what I want)'}\n"
            "Use join_lunch, find what's available with find_restaurants / get_menu / "
            "search_dishes, suggest a few options, and add my choice with pick_dish. "
            "When I'm happy, finish_my_order."
        )

    return server


def _dishes(menu: dict[str, Any], search: str | None) -> list[dict[str, Any]]:
    wanted = (search or "").strip().lower()
    return [
        {
            "dish_id": item["id"],
            "dish": item["name"],
            "price": helpers.rupees(item.get("price_paise")),
            "veg": item.get("veg"),
            "in_stock": item.get("in_stock", True),
            "section": category["name"],
        }
        for category in menu.get("categories", [])
        for item in category.get("items", [])
        if not wanted or wanted in item["name"].lower()
    ]


def _choose(dishes: list[dict[str, Any]], dish: str) -> dict[str, Any]:
    """The one dish named `dish` (an id, or a name that matches exactly one)."""
    by_id = [d for d in dishes if d["dish_id"] == dish]
    if by_id:
        return by_id[0]
    said = dish.strip().lower()
    exact = [d for d in dishes if d["dish"].lower() == said]
    matches = exact or [d for d in dishes if said in d["dish"].lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ToolError(f"This restaurant has no dish like '{dish}'. Try get_menu.")
    names = ", ".join(f"{d['dish']} ({d['dish_id']})" for d in matches[:8])
    raise ToolError(f"'{dish}' matches several dishes: {names}. Say which one.")
