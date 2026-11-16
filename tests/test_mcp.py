"""The MCP server, end to end: an MCP client calls its tools, which call the real REST
API (its auth, permissions and database); Swiggy and place search are faked."""

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from rest_framework.test import APIClient

from apps.accounts import services as accounts
from apps.catalog import swiggy
from apps.lunches.models import OrderSession
from mcp_server import helpers
from mcp_server.server import Config, create_server
from tests.test_parties import person
from tests.test_places import PHOTON, serve

# TRUNCATE-free, but the app runs in another thread (the Django ORM refuses to run on an
# event loop), so its writes must be committed to be seen from here.
pytestmark = pytest.mark.django_db(transaction=True)

IST = ZoneInfo("Asia/Kolkata")
NOON = datetime(2026, 11, 16, 11, 0, tzinfo=IST)

MENU = {
    "restaurant": {"id": "100", "name": "Chicking"},
    "categories": [
        {
            "name": "Chicken",
            "items": [
                {
                    "id": "d1",
                    "name": "Chick Pop (M)",
                    "price_paise": 24900,
                    "veg": False,
                },
                {
                    "id": "d2",
                    "name": "Chick Pop (L)",
                    "price_paise": 34900,
                    "veg": False,
                },
                {
                    "id": "d3",
                    "name": "Zinger Burger",
                    "price_paise": 17900,
                    "veg": False,
                },
                {
                    "id": "d4",
                    "name": "Spicy Wings",
                    "price_paise": 19900,
                    "in_stock": False,
                },
            ],
        }
    ],
}
ZINGER = {"id": "d3", "name": "Zinger Burger", "price_paise": 17900, "veg": False}
RESTAURANT = {
    "id": "100",
    "name": "Chicking",
    "area": "Kakkanad",
    "cuisines": ["Fast Food"],
    "rating": 4.2,
    "delivery_time": "25 mins",
    "distance_km": 0.9,
    "open": True,
}


class DjangoTransport(httpx.AsyncBaseTransport):
    """Sends the MCP server's API calls to the Django app, in a thread (the ORM can't
    run on the event loop), exactly as a real request would arrive."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        def run():
            return APIClient().generic(
                request.method,
                request.url.raw_path.decode(),
                data=request.content,
                content_type="application/json",
                HTTP_AUTHORIZATION=request.headers.get("authorization", ""),
            )

        response = await asyncio.to_thread(run)
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers={"content-type": "application/json"},
        )


@pytest.fixture
def world(monkeypatch):
    """Swiggy and the place search, faked."""
    serve(monkeypatch, photon=PHOTON)
    monkeypatch.setattr(swiggy, "nearby_restaurants", lambda lat, lng: [RESTAURANT])
    monkeypatch.setattr(swiggy, "search_restaurants", lambda q, lat, lng: [RESTAURANT])
    monkeypatch.setattr(swiggy, "menu", lambda rid, lat, lng: MENU)
    monkeypatch.setattr(
        swiggy,
        "search_dishes",
        lambda q, lat, lng: [{**ZINGER, "restaurant": RESTAURANT}],
    )


_tokens: dict = {}


@pytest.fixture(autouse=True)
def fresh_tokens():
    _tokens.clear()


def token_for(user):
    """One token per person per test, as a real assistant keeps its own."""
    if user.id not in _tokens:
        _tokens[user.id] = accounts.issue_api_token(user, "test", timedelta(days=30))[1]
    return _tokens[user.id]


def config(user=None, **extra):
    return Config(
        api_url="http://testserver",
        web_url="https://lunch.example",
        env_token=token_for(user) if user else None,
        transport=DjangoTransport(),
        **extra,
    )


def first_text(thing) -> str:
    """The text of a tool result's, or a prompt message's, first part."""
    part = thing.content[0] if hasattr(thing.content, "__getitem__") else thing.content
    return getattr(part, "text", "")


async def call(cfg, tool, **arguments):
    """Call a tool; returns its result as data, or raises what the person would see as
    an error."""
    async with Client(create_server(cfg)) as client:
        result = await client.call_tool(tool, arguments)
    if result.is_error:
        raise ToolError(first_text(result))
    if result.structured_content is not None:
        data = result.structured_content
        return data.get("result", data)
    return json.loads(first_text(result))


def failing(user, tool, **arguments):
    try:
        asyncio.run(call(config(user), tool, **arguments))
    except ToolError as error:
        return str(error)
    raise AssertionError("expected an error")


def run(user, tool, **arguments):
    return asyncio.run(call(config(user), tool, **arguments))


# ---- the server itself ----


def test_every_tool_is_listed_with_honest_hints():
    async def tools():
        async with Client(create_server(config())) as client:
            return {t.name: t for t in (await client.list_tools()).tools}

    listed = asyncio.run(tools())

    assert {
        "start_lunch",
        "share_lunch",
        "join_lunch",
        "get_lunch",
        "pick_dish",
    } <= set(listed)
    assert listed["get_lunch"].annotations.read_only_hint is True
    assert listed["confirm_lunch"].annotations.destructive_hint is True
    assert listed["start_lunch"].annotations.read_only_hint is False


def test_prompts_guide_a_host_and_a_guest():
    async def prompts():
        async with Client(create_server(config())) as client:
            names = {p.name for p in (await client.list_prompts()).prompts}
            text = await client.get_prompt(
                "start_team_lunch", {"closes_at": "12:30", "place": "Infopark"}
            )
            return names, first_text(text.messages[0])

    names, text = asyncio.run(prompts())

    assert names == {"start_team_lunch", "pick_my_lunch"}
    assert "12:30" in text and "Infopark" in text and "start_lunch" in text


def test_no_token_means_a_clear_message(db):
    assert "not signed in to Tiffmate" in failing(None, "whoami")


def test_a_rejected_token_says_to_connect_again(db):
    async def go():
        cfg = config()
        cfg.env_token = "lc_" + "x" * 43
        async with Client(create_server(cfg)) as client:
            return await client.call_tool("whoami", {})

    result = asyncio.run(go())

    assert result.is_error and "connect the assistant again" in first_text(result)


def test_a_revoked_token_stops_the_assistant(member):
    token = token_for(member)
    from apps.accounts.models import ApiToken

    ApiToken.objects.update(revoked_at=datetime.now(IST))

    async def go():
        cfg = config()
        cfg.env_token = token
        async with Client(create_server(cfg)) as client:
            return await client.call_tool("whoami", {})

    assert asyncio.run(go()).is_error


DECOY = "lc_a_token_in_the_servers_environment"


def http_call(member_token, tool, *, decoy=DECOY):
    """Call a tool the way a web client does: JSON-RPC over HTTP with its own header."""
    from mcp.server.transport_security import TransportSecuritySettings

    server = create_server(
        Config(
            api_url="http://testserver",
            env_token=decoy,
            transport=DjangoTransport(),
        )
    )
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )

    async def go():
        headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
        }
        if member_token:
            headers["authorization"] = f"Bearer {member_token}"
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as http,
        ):
            return await http.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": {}},
                },
            )

    return asyncio.run(go())


def test_over_http_each_caller_acts_as_themselves(member, host):
    mine = http_call(token_for(member), "whoami")
    theirs = http_call(token_for(host), "whoami")

    assert mine.status_code == 200
    assert member.email in mine.text
    assert host.email in theirs.text
    assert host.email not in mine.text


def test_over_http_the_servers_own_token_is_never_used_for_a_caller(member):
    """A shared server must not fall back to a token in its environment: that would let
    anyone who can reach it act as that person."""
    response = http_call(None, "whoami", decoy=token_for(member))

    assert response.status_code == 200
    assert member.email not in response.text
    assert "not signed in to Tiffmate" in response.text


def test_over_http_a_bad_token_is_refused(db):
    response = http_call("lc_" + "x" * 43, "whoami")

    assert "connect the assistant again" in response.text


# ---- signing in: the server tells assistants where, and checks the token ----

PUBLIC = "https://mcp.example"
ISSUER = "https://api.example"
RPC = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def gated(**extra):
    from mcp_server.web import build_app

    return build_app(
        Config(api_url="http://testserver", transport=DjangoTransport()),
        public_url=PUBLIC,
        issuer=ISSUER,
        allowed_hosts=[],
        **extra,
    )


def gate_call(app, method="POST", path="/mcp", token=None, body=RPC):
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    if token:
        headers["authorization"] = f"Bearer {token}"

    async def go():
        async with (
            app.router.lifespan_context(app.app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as http,
        ):
            return await http.request(method, path, headers=headers, json=body)

    return asyncio.run(go())


def test_the_server_says_where_to_sign_in(db):
    for path in (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ):
        response = gate_call(gated(), "GET", path, body=None)

        assert response.status_code == 200
        assert response.json() == {
            "resource": "https://mcp.example/mcp",
            "authorization_servers": ["https://api.example"],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["lunch"],
            "resource_name": "Tiffmate",
        }


def test_a_call_with_no_token_is_told_to_sign_in(db):
    response = gate_call(gated())

    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer resource_metadata=")
    assert "https://mcp.example/.well-known/oauth-protected-resource/mcp" in challenge
    assert 'scope="lunch"' in challenge and "invalid_token" not in challenge
    assert response.headers["cache-control"] == "no-store"


def test_a_token_the_api_refuses_is_a_401_so_the_assistant_signs_in_again(db):
    response = gate_call(gated(), token="lc_" + "x" * 43)

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


def test_a_valid_token_goes_through_to_the_tools(member):
    response = gate_call(gated(), token=token_for(member))

    assert response.status_code == 200
    assert "start_lunch" in response.text


def test_a_token_checked_a_moment_ago_is_not_checked_again(member):
    from apps.accounts.models import ApiToken

    app = gated()
    token = token_for(member)
    assert asyncio.run(app._valid(token)) is True
    ApiToken.objects.update(revoked_at=datetime.now(IST))

    trusted = asyncio.run(app._valid(token))  # within the minute it is trusted
    app._checked.clear()
    after = asyncio.run(app._valid(token))

    assert trusted is True
    assert after is False


def test_when_the_api_is_unreachable_the_gate_does_not_force_a_sign_in_loop(db):
    from mcp_server.web import build_app

    class Down(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("down")

    app = build_app(
        Config(api_url="http://testserver", transport=Down()),
        public_url=PUBLIC,
        issuer=ISSUER,
        allowed_hosts=[],
    )

    assert gate_call(app, token="lc_" + "y" * 43).status_code == 200


def test_the_public_address_can_come_from_the_request_when_not_set(db):
    from mcp_server.web import build_app

    app = build_app(
        Config(api_url="http://testserver", transport=DjangoTransport()),
        issuer=ISSUER,
        allowed_hosts=[],
    )

    resource = gate_call(
        app, "GET", "/.well-known/oauth-protected-resource", body=None
    ).json()

    assert resource["resource"] == "http://testserver/mcp"


def test_the_deployable_web_app_is_gated_too():
    from mcp_server.web import app

    assert gate_call(app).status_code == 401


# ---- understanding people ----


@pytest.mark.parametrize(
    "said, expected",
    [
        ("12:30", datetime(2026, 11, 16, 12, 30, tzinfo=IST)),
        ("12:30 pm", datetime(2026, 11, 16, 12, 30, tzinfo=IST)),
        ("1 PM", datetime(2026, 11, 16, 13, 0, tzinfo=IST)),
        (
            "9:15 am",
            datetime(2026, 11, 17, 9, 15, tzinfo=IST),
        ),  # already past: tomorrow
        ("11:01", datetime(2026, 11, 17, 11, 1, tzinfo=IST)),  # too close to now
        ("in 90 minutes", datetime(2026, 11, 16, 12, 30, tzinfo=IST)),
        ("in 2 hours", datetime(2026, 11, 16, 13, 0, tzinfo=IST)),
        ("in 1.5 h", datetime(2026, 11, 16, 12, 30, tzinfo=IST)),
        ("2026-11-16T14:00:00", datetime(2026, 11, 16, 14, 0, tzinfo=IST)),
    ],
)
def test_closing_times_people_say(said, expected):
    assert helpers.parse_closing_time(said, NOON) == expected


@pytest.mark.parametrize("said", ["lunchtime", "25:00", "13 pm", "12:75", ""])
def test_a_closing_time_that_makes_no_sense_is_refused(said):
    with pytest.raises(ToolError, match="couldn't read"):
        helpers.parse_closing_time(said, NOON)


def test_a_lunch_is_found_in_a_link_or_an_id():
    lid = "123e4567-e89b-42d3-a456-426614174000"

    assert helpers.lunch_id_from(lid) == lid
    assert helpers.lunch_id_from(f"https://x.app/#/lunch/{lid}") == lid
    with pytest.raises(ToolError):
        helpers.lunch_id_from("not a lunch")


def test_a_relative_closing_time_counts_from_now(world):

    from django.utils import timezone

    run(
        person("hana"),
        "start_lunch",
        closes_at="in 90 minutes",
        delivery_place="Infopark",
    )

    gap = OrderSession.objects.get().cutoff_at - timezone.now()
    assert timedelta(minutes=88) < gap < timedelta(minutes=91)


# ---- a whole lunch, as a company host and a team ----


def test_a_gmail_host_starts_a_lunch_from_a_prompt_and_shares_it(world):
    host = person("hana")

    started = run(
        host,
        "start_lunch",
        closes_at="12:30",
        delivery_place="Infopark",
        name="Friday biryani",
    )

    lunch = OrderSession.objects.get()
    assert started["lunch_id"] == str(lunch.id)
    assert started["share_link"] == f"https://lunch.example/#/lunch/{lunch.id}"
    assert started["picks_close"].startswith("12:30 pm")
    assert "Infopark" in started["delivering_to"]
    assert lunch.name == "Friday biryani"
    assert lunch.address.lat == pytest.approx(10.0105236)
    assert lunch.host == host

    shared = run(host, "share_lunch", lunch_id=started["lunch_id"])
    assert shared["share_link"] == started["share_link"]
    assert "Hana started Friday biryani" in shared["message"]
    assert started["share_link"] in shared["message"]


def test_a_company_member_who_cannot_start_lunches_is_told_why(member, world):
    message = failing(
        member, "start_lunch", closes_at="12:30", delivery_place="Infopark"
    )

    assert OrderSession.objects.count() == 0
    assert "role doesn't let you start a food party" in message.lower()


def test_a_company_host_starts_a_lunch(host, world):
    run(host, "start_lunch", closes_at="12:30", delivery_place="Infopark")

    assert OrderSession.objects.get().org == host.org


def test_an_unknown_place_asks_for_another(host, monkeypatch):
    serve(monkeypatch, photon={"features": []})

    message = failing(host, "start_lunch", closes_at="12:30", delivery_place="zzzz")

    assert "couldn't find 'zzzz'" in message


def test_a_closing_time_over_a_day_away_is_refused(host, world):
    message = failing(
        host, "start_lunch", closes_at="2026-11-18T12:00:00", delivery_place="Infopark"
    )

    assert "24 hours" in message


def test_the_places_tool_lists_matches_with_coordinates(host, world):
    found = run(host, "search_places", query="infopark")

    assert found[0]["name"].startswith("Infopark Kochi")
    assert found[0]["lat"] == pytest.approx(10.0105236)


def test_whoami_says_who_the_assistant_is_acting_as(host, world):
    me = run(host, "whoami")

    assert me["email"] == host.email
    assert me["can_start_lunches"] is True
    assert run(person("gus"), "whoami")["can_start_lunches"] is True


def started(host):
    return run(host, "start_lunch", closes_at="12:30", delivery_place="Infopark")


def test_a_guest_joins_picks_dishes_and_finishes(world):
    host, guest = person("hana"), person("gus")
    link = started(host)["share_link"]

    joined = run(guest, "join_lunch", lunch=link)
    assert joined["host"] == "Hana"
    lunch_id = joined["lunch_id"]
    assert [x["joined_as_guest"] for x in run(guest, "list_lunches")] == [True]

    restaurants = run(guest, "find_restaurants", lunch_id=lunch_id)
    assert restaurants[0]["name"] == "Chicking"
    menu = run(guest, "get_menu", restaurant_id="100", lunch_id=lunch_id, search="pop")
    assert [d["dish"] for d in menu["dishes"]] == ["Chick Pop (M)", "Chick Pop (L)"]
    assert menu["dishes"][0]["price"] == 249.0

    picked = run(
        guest,
        "pick_dish",
        lunch_id=lunch_id,
        restaurant_id="100",
        dish="Chick Pop (M)",
        quantity=2,
        note="extra crispy",
    )
    assert picked["added"] == "2 x Chick Pop (M) from Chicking"
    assert picked["price_each"] == 249.0

    board = run(host, "get_lunch", lunch_id=lunch_id)
    assert board["picks"][0]["person"] == "Gus"
    assert board["picks"][0]["total"] == 498.0
    assert board["picks"][0]["dishes"][0]["note"] == "extra crispy"
    assert board["by_restaurant"] == {
        "Chicking": [{"dish": "Chick Pop (M)", "quantity": 2}]
    }
    assert board["items_total"] == 498.0

    done = run(guest, "finish_my_order", lunch_id=lunch_id)
    assert done["picks"][0]["finished"] is True


def test_picking_a_dish_by_id_and_removing_it(world):
    host = person("hana")
    lunch_id = started(host)["lunch_id"]

    picked = run(host, "pick_dish", lunch_id=lunch_id, restaurant_id="100", dish="d3")
    run(host, "remove_pick", lunch_id=lunch_id, item_id=picked["item_id"])

    assert run(host, "get_lunch", lunch_id=lunch_id)["picks"] == []


def test_a_vague_or_unavailable_dish_is_explained(world):
    host = person("hana")
    lunch_id = started(host)["lunch_id"]

    assert "matches several dishes" in failing(
        host, "pick_dish", lunch_id=lunch_id, restaurant_id="100", dish="chick pop"
    )
    assert "no dish like" in failing(
        host, "pick_dish", lunch_id=lunch_id, restaurant_id="100", dish="sushi"
    )
    assert "out of stock" in failing(
        host, "pick_dish", lunch_id=lunch_id, restaurant_id="100", dish="Spicy Wings"
    )


def test_dishes_can_be_searched_near_the_delivery_place(world):
    host = person("hana")
    lunch_id = started(host)["lunch_id"]

    found = run(host, "search_dishes", query="zinger", lunch_id=lunch_id)

    assert found[0]["dish"] == "Zinger Burger"
    assert found[0]["restaurant"] == "Chicking"


def test_restaurants_can_be_found_near_a_named_place_or_coordinates(world):
    host = person("hana")

    assert run(host, "find_restaurants", near="Infopark")[0]["name"] == "Chicking"
    assert run(host, "find_restaurants", lat=10.0, lng=76.3, search="chick")
    assert "somewhere to look near" in failing(host, "find_restaurants")


def test_the_host_can_close_the_share_link_and_a_newcomer_is_told(world):
    host, newcomer = person("hana"), person("gus")
    link = started(host)["share_link"]

    closed = run(host, "set_link_open", lunch_id=link, allow_new_people=False)
    assert closed["link_open_to_new_people"] is False
    assert "closed this food party to new people" in failing(
        newcomer, "join_lunch", lunch=link
    )

    reopened = run(host, "set_link_open", lunch_id=link, allow_new_people=True)
    assert reopened["link_open_to_new_people"] is True
    assert run(newcomer, "join_lunch", lunch=link)["host"] == "Hana"


def test_skipping_and_rejoining(world):
    host, guest = person("hana"), person("gus")
    lunch_id = run(guest, "join_lunch", lunch=started(host)["share_link"])["lunch_id"]

    assert "skipping" in run(guest, "skip_lunch", lunch_id=lunch_id)
    assert run(host, "get_lunch", lunch_id=lunch_id)["skipping"] == ["Gus"]


def test_the_host_locks_confirms_and_records_the_order(host, member, world):
    lunch_id = started(host)["lunch_id"]
    run(host, "pick_dish", lunch_id=lunch_id, restaurant_id="100", dish="d3")

    locked = run(host, "lock_lunch", lunch_id=lunch_id)
    assert locked["status"] == "locked"
    unlocked = run(host, "unlock_lunch", lunch_id=lunch_id)
    assert unlocked["status"] == "open"
    run(host, "lock_lunch", lunch_id=lunch_id)

    assert "Not confirmed" in failing(host, "confirm_lunch", lunch_id=lunch_id)
    confirmed = run(host, "confirm_lunch", lunch_id=lunch_id, confirm=True)
    assert confirmed["status"] == "confirmed"
    order = run(host, "get_lunch", lunch_id=lunch_id)["orders"][0]  # the job has run
    assert order["status"] == "needs_host"
    assert "waiting for Swiggy approval" in order["note"]

    run(
        host,
        "mark_order_placed",
        order_id=order["order_id"],
        order_number="184220973",
    )
    placed = run(host, "get_lunch", lunch_id=lunch_id)
    assert placed["orders"][0]["order_number"] == "184220973"
    run(host, "mark_order_arrived", order_id=order["order_id"])
    assert (
        run(host, "get_lunch", lunch_id=lunch_id)["orders"][0]["status"] == "delivered"
    )


def test_a_guest_cannot_run_the_hosts_lunch(world):
    host, guest = person("hana"), person("gus")
    lunch_id = run(guest, "join_lunch", lunch=started(host)["share_link"])["lunch_id"]

    for tool in ("lock_lunch", "unlock_lunch"):
        assert failing(guest, tool, lunch_id=lunch_id)
    assert failing(guest, "confirm_lunch", lunch_id=lunch_id, confirm=True)
    assert OrderSession.objects.get().status == "open"


def test_calling_a_lunch_off_needs_a_yes(world):
    host = person("hana")
    lunch_id = started(host)["lunch_id"]

    assert "Not confirmed" in failing(host, "cancel_lunch", lunch_id=lunch_id)
    called_off = run(host, "cancel_lunch", lunch_id=lunch_id, confirm=True)
    assert called_off["status"] == "cancelled"


def test_the_host_hears_about_joins_and_can_clear_them(world):
    host, guest = person("hana"), person("gus")
    link = started(host)["share_link"]
    run(guest, "join_lunch", lunch=link)

    heard = run(host, "my_notifications")

    assert heard["unread"] >= 1
    assert "Gus" in heard["items"][0]["title"] + heard["items"][0]["body"]
    run(host, "mark_notifications_read")
    assert run(host, "my_notifications")["unread"] == 0


def test_a_lunch_that_does_not_exist_is_not_found(world):
    host = person("hana")

    message = failing(
        host, "get_lunch", lunch_id="123e4567-e89b-42d3-a456-426614174000"
    )

    assert "couldn't find" in message.lower() or "not found" in message.lower()


# ---- local menus ----

PHOTO_MENU = [
    {"name": "Meals", "price": 90, "veg": True},
    {"name": "Fish fry", "price": 150.5},
]


def test_an_assistant_saves_a_menu_it_read_from_a_photo(host):
    saved = run(
        host,
        "add_local_menu",
        restaurant_name="Amma's Mess",
        items=PHOTO_MENU,
        rating=4.3,
        distance_km=1.4,
    )

    assert saved["dishes"] == 2 and saved["menu_id"]
    listed = run(host, "list_local_menus")
    assert listed[0]["restaurant"] == "Amma's Mess"
    assert {d["name"]: d["price"] for d in listed[0]["dishes"]} == {
        "Meals": 90,
        "Fish fry": 150.5,
    }


def test_a_menu_needs_a_name_and_a_priced_dish(host):
    assert "name" in failing(
        host, "add_local_menu", restaurant_name="", items=PHOTO_MENU
    )
    assert "items" in failing(
        host, "add_local_menu", restaurant_name="Amma's Mess", items=[]
    )


def test_a_party_is_started_with_a_saved_local_menu(host, world):
    saved = run(host, "add_local_menu", restaurant_name="Amma's Mess", items=PHOTO_MENU)

    started = run(
        host,
        "start_lunch",
        closes_at="12:30",
        delivery_place="Infopark",
        local_menu_ids=[saved["menu_id"]],
    )

    lunch = OrderSession.objects.get(id=started["lunch_id"])
    assert lunch.offered_restaurants[0]["name"] == "Amma's Mess"
    assert lunch.offered_restaurants[0]["local"] is True


def test_deleting_a_menu_needs_a_yes(host):
    saved = run(host, "add_local_menu", restaurant_name="Amma's Mess", items=PHOTO_MENU)

    assert "Not confirmed" in failing(
        host, "delete_local_menu", menu_id=saved["menu_id"]
    )
    assert run(host, "delete_local_menu", menu_id=saved["menu_id"], confirm=True)
    assert run(host, "list_local_menus") == []


def test_a_member_cannot_save_a_menu(member):
    assert "Only hosts" in failing(
        member, "add_local_menu", restaurant_name="X", items=PHOTO_MENU
    )
