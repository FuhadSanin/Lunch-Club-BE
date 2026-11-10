import re
import sys
import types
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest

from apps.accounts.models import Permission
from apps.ordering import bot_status, swiggy_web
from apps.ordering.progress import Progress
from apps.ordering.providers.base import CartLine, HostActionRequired, PlaceRequest
from apps.ordering.providers.swiggy_web import SwiggyWebProvider
from tests.conftest import client_for

LINES = (
    CartLine("Chicking", "Zinger Burger", 2, "", "111"),
    CartLine("Chicking", "Fries", 1, "", "111"),
    CartLine("Biryani Life", "Lucknowi Biryani", 3, "", ""),
)


def request(lines=LINES):
    return PlaceRequest(
        order_id=uuid4(),
        host_id=uuid4(),
        delivery_address="Office",
        lines=lines,
        total_paise=1000,
    )


def statuses(progress):
    return {step["key"]: step["status"] for step in progress.steps}


# --- A tiny stand-in for a Playwright page ------------------------------------------


class Loc:
    """A locator: `n` matches, each answering with `text`/`label`; chains to itself."""

    def __init__(self, n=0, text="", label="", children=None, log=None):
        self.n, self.text, self.label = n, text, label
        self.children = children or {}
        self.log = log if log is not None else []
        self.visible = True
        self._first = None

    def count(self):
        return self.n

    @property
    def first(self):
        return self._first or self

    @first.setter
    def first(self, value):
        self._first = value

    @property
    def last(self):
        return self

    def nth(self, i):
        return self.children.get(("nth", i), self)

    def locator(self, selector):
        return self.children.get(selector, Loc(log=self.log))

    def filter(self, has=None):
        return self

    def get_by_role(self, role, **kwargs):
        return self.children.get(role, Loc(log=self.log))

    def get_by_text(self, text, **kwargs):
        return self.children.get(str(getattr(text, "pattern", text)), Loc(log=self.log))

    def click(self):
        self.log.append("click")

    def fill(self, value):
        self.log.append(f"fill:{value}")

    def wait_for(self, **kwargs):
        pass

    def is_visible(self):
        return self.visible

    def inner_text(self):
        return self.text

    def text_content(self):
        return self.text

    def get_attribute(self, name):
        return self.label


class Page(Loc):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.visited = []
        self.context = types.SimpleNamespace(new_page=lambda: self)
        self.closed = False

    def goto(self, url, timeout=0):
        self.visited.append(url)

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, script):
        self.log.append("scroll")

    def close(self):
        self.closed = True


def present(text, n=1):
    return Loc(n)


# --- The provider -------------------------------------------------------------------


@pytest.fixture
def browser(monkeypatch):
    """Replace the real browser and cart filling, and record what was asked."""
    seen = {}

    @contextmanager
    def fake_browser_page():
        yield "the page"

    def fake_fill_cart(page, lines, *, require_login, progress, policy):
        seen.update(page=page, lines=lines, login=require_login, policy=policy)
        for key in statuses(progress):
            if key not in ("browser", "handoff"):
                progress.start(key)
                progress.done(key)

    monkeypatch.setattr(swiggy_web, "browser_page", fake_browser_page)
    monkeypatch.setattr(swiggy_web, "fill_cart", fake_fill_cart)
    return seen


def test_a_filled_cart_is_handed_to_the_host_to_pay(browser, settings):
    settings.SWIGGY_BOT_REQUIRE_LOGIN = False
    settings.SWIGGY_BOT_REPLACE_CART = True
    settings.SWIGGY_BOT_AUTO_OPTIONS = True
    progress = Progress()

    with pytest.raises(HostActionRequired, match="check out"):
        SwiggyWebProvider().place(request(), progress)

    assert browser["page"] == "the page"
    assert browser["login"] is False
    assert browser["policy"] == swiggy_web.Policy(True, True)
    assert set(statuses(progress).values()) == {"done"}


def test_a_cart_problem_reaches_the_host_unchanged(monkeypatch):
    @contextmanager
    def fake_browser_page():
        yield "the page"

    def broken(*args, **kwargs):
        raise HostActionRequired("Fries isn't in the cart")

    monkeypatch.setattr(swiggy_web, "browser_page", fake_browser_page)
    monkeypatch.setattr(swiggy_web, "fill_cart", broken)

    with pytest.raises(HostActionRequired, match="Fries isn't in the cart"):
        SwiggyWebProvider().place(request(), Progress())


def test_there_is_never_an_order_to_find_after_a_crash():
    assert SwiggyWebProvider().find_existing(request()) is None
    assert SwiggyWebProvider.sequential is True


def test_confirming_fills_the_cart_and_asks_the_host_to_check_out(
    browser, settings, host, lunch, django_capture_on_commit_callbacks
):
    from apps.lunches.models import OrderItem

    settings.ORDER_PROVIDER = "apps.ordering.providers.swiggy_web.SwiggyWebProvider"
    from apps.ordering.providers import get_provider

    get_provider.cache_clear()
    try:
        OrderItem.objects.create(
            session=lunch,
            user=host,
            restaurant_name="Chicking",
            restaurant_id="111",
            dish_name="Zinger Burger",
            quantity=1,
            price_paise=10000,
        )
        client = client_for(host)
        assert client.post(f"/api/lunches/{lunch.id}/lock").status_code == 200
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(f"/api/lunches/{lunch.id}/confirm")
        assert response.status_code == 200
        order = client.get(f"/api/lunches/{lunch.id}").json()["orders"][0]
        assert order["status"] == "needs_host"
        assert "check out" in order["last_error"]
        assert order["bot_steps"][-1]["key"] == "handoff"
        assert order["bot_steps"][-1]["status"] == "done"
    finally:
        get_provider.cache_clear()


# --- Steps --------------------------------------------------------------------------


def test_every_step_is_announced_with_its_reason():
    steps = swiggy_web.plan_steps(LINES)

    keys = [key for key, _, _ in steps]
    assert keys[:2] == ["browser", "login"]
    assert "open:Chicking" in keys and "open:Biryani Life" in keys
    assert "add:Chicking:Fries" in keys
    assert keys[-2:] == ["verify", "handoff"]
    assert all(label and why for _, label, why in steps)
    assert swiggy_web.add_key(LINES[0]) == "add:Chicking:Zinger Burger"


def test_a_failing_step_records_why_and_the_rest_stay_pending():
    progress = Progress()
    progress.plan(swiggy_web.plan_steps(LINES))

    with pytest.raises(HostActionRequired), progress.step("browser"):
        raise HostActionRequired("Can't reach Chrome")

    assert progress.steps[0]["status"] == "failed"
    assert progress.steps[0]["detail"] == "Can't reach Chrome"
    assert set(statuses(progress).values()) == {"failed", "pending"}


def test_an_unexpected_error_is_recorded_in_plain_words():
    progress = Progress()
    progress.plan(swiggy_web.plan_steps(LINES))

    with pytest.raises(ValueError, match="boom"), progress.step("browser"):
        raise ValueError("boom\nsecond line")

    assert progress.steps[0]["detail"] == "ValueError: boom"


def test_the_host_sees_the_checklist_and_where_it_failed(
    fake_provider, host, lunch, django_capture_on_commit_callbacks
):
    from apps.lunches.models import OrderItem

    OrderItem.objects.create(
        session=lunch,
        user=host,
        restaurant_name="Chicking",
        dish_name="Fries",
        quantity=1,
        price_paise=5000,
    )
    assert client_for(host).post(f"/api/lunches/{lunch.id}/lock").status_code == 200
    fake_provider.error = HostActionRequired("Nope")

    client = client_for(host)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(f"/api/lunches/{lunch.id}/confirm")

    order = client.get(f"/api/lunches/{lunch.id}").json()["orders"][0]
    assert order["status"] == "needs_host"
    assert order["last_error"] == "Nope"


# --- Bot status ---------------------------------------------------------------------


def test_bot_status_lists_each_check_with_a_fix_when_chrome_is_down(
    host, settings, monkeypatch
):
    settings.ORDER_PROVIDER = "tests.fakes.FakeProvider"
    settings.SWIGGY_BOT_CDP_URL = "http://127.0.0.1:9222"
    monkeypatch.setattr(bot_status, "chrome_version", lambda: None)
    monkeypatch.setattr(bot_status, "in_docker", lambda: True)
    monkeypatch.setattr(bot_status.importlib.util, "find_spec", lambda name: None)

    body = client_for(host).get("/api/bot/status").json()

    assert body["ready"] is False
    checks = {item["key"]: item for item in body["checks"]}
    assert set(checks) == {"provider", "runtime", "playwright", "chrome"}
    assert not any(item["ok"] for item in checks.values())
    assert checks["runtime"]["fix"] == "make bot-api"
    assert "remote-debugging-port=9222" in checks["chrome"]["fix"]
    assert [c["command"] for c in body["commands"]][2].endswith("/json/version")


def test_bot_status_reports_the_browser_when_chrome_is_listening(
    host, settings, monkeypatch
):
    settings.ORDER_PROVIDER = "apps.ordering.providers.swiggy_web.SwiggyWebProvider"
    settings.SWIGGY_BOT_CDP_URL = "http://127.0.0.1:9333"
    monkeypatch.setattr(bot_status, "chrome_version", lambda: "Chrome/130")
    monkeypatch.setattr(bot_status, "in_docker", lambda: False)
    monkeypatch.setattr(bot_status.importlib.util, "find_spec", lambda name: object())

    body = client_for(host).get("/api/bot/status").json()

    assert body["ready"] is True
    chrome = body["checks"][-1]
    assert chrome["detail"] == "Connected to Chrome/130"
    assert chrome["fix"] == ""
    assert "9333" in bot_status.chrome_command()


def test_bot_status_without_a_debug_port_skips_the_chrome_check(settings):
    settings.SWIGGY_BOT_CDP_URL = ""

    assert "chrome" not in [item["key"] for item in bot_status.checks()]
    assert "9222" in bot_status.chrome_command()


def test_chrome_version_reads_the_debug_port(settings, monkeypatch):
    settings.SWIGGY_BOT_CDP_URL = "http://127.0.0.1:9222"

    def reply(json):
        return lambda url, timeout: types.SimpleNamespace(json=lambda: json)

    monkeypatch.setattr(httpx, "get", reply({"Browser": "Chrome/1"}))
    assert bot_status.chrome_version() == "Chrome/1"
    monkeypatch.setattr(httpx, "get", reply({}))
    assert bot_status.chrome_version() == "unknown browser"

    def refuse(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", refuse)
    assert bot_status.chrome_version() is None


def test_docker_is_detected_from_its_marker_file():
    assert isinstance(bot_status.in_docker(), bool)


def test_members_cannot_see_the_bot_status(member):
    assert Permission.ORDER_UPDATE not in member.permissions
    assert client_for(member).get("/api/bot/status").status_code == 403


def test_the_login_step_is_marked_skipped_when_the_check_is_off(monkeypatch):
    page = Page()
    monkeypatch.setattr(swiggy_web, "_by_restaurant", lambda lines: [])
    progress = Progress()
    progress.plan(swiggy_web.plan_steps(()))
    monkeypatch.setattr(swiggy_web, "_verify_cart", lambda page, lines: None)

    swiggy_web.fill_cart(page, (), require_login=False, progress=progress)

    assert progress.steps[1]["status"] == "done"
    assert progress.steps[1]["detail"].startswith("Skipped")
    assert page.visited == [swiggy_web.SWIGGY]


def test_a_signed_out_browser_stops_the_bot_when_login_is_required():
    page = Page(children={"Sign In": Loc(1)})

    with pytest.raises(HostActionRequired, match="signed out"):
        swiggy_web._check_logged_in(page, True)
    swiggy_web._check_logged_in(page, False)


# --- Options and quantities ---------------------------------------------------------


def test_options_default_to_the_cheapest_which_is_usually_free():
    options = [("Large", 40), ("Regular", 0), ("Medium", 20)]

    assert swiggy_web.pick_options(options, 1, "") == ["Regular"]


def test_an_option_named_in_the_note_wins():
    options = [("Large", 40), ("Regular", 0)]

    assert swiggy_web.pick_options(options, 1, "please make it large") == ["Large"]


def test_a_group_needing_more_than_it_offers_is_not_guessed():
    assert swiggy_web.pick_options([("Only", 0)], 2, "") is None


def test_a_group_can_need_several_options():
    options = [("A", 10), ("B", 0), ("C", 5)]

    assert swiggy_web.pick_options(options, 2, "") == ["B", "C"]


def test_the_cart_quantity_is_read_from_the_checkout_page():
    text = "2 Non-Veg Lucknowi Mini Biryani\nCustomize | + | 3 | 1134"

    assert swiggy_web.cart_quantity(text, "2 Non-Veg Lucknowi Mini Biryani") == 3
    assert swiggy_web.cart_quantity(text, "Something else") is None
    assert swiggy_web.norm("McDonald's  Burger!") == "mcdonald s burger"


# --- Driving a page -----------------------------------------------------------------


def test_the_browser_needs_playwright_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    with pytest.raises(HostActionRequired, match="make bot-api"):  # noqa: SIM117
        with swiggy_web.browser_page():
            pass


def fake_playwright(monkeypatch, *, fail=None, pages=None):
    page = Page()
    context = types.SimpleNamespace(
        new_page=lambda: page,
        pages=pages if pages is not None else [],
        close=page.close,
    )
    browser = types.SimpleNamespace(contexts=[context])

    def connect(url):
        if fail:
            raise fail
        return browser

    chromium = types.SimpleNamespace(
        connect_over_cdp=connect, launch_persistent_context=lambda *a, **k: context
    )

    @contextmanager
    def sync_playwright():
        yield types.SimpleNamespace(chromium=chromium)

    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = sync_playwright
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return page


def test_the_bot_opens_its_own_tab_in_the_hosts_chrome(monkeypatch, settings):
    settings.SWIGGY_BOT_CDP_URL = "http://127.0.0.1:9222"
    page = fake_playwright(monkeypatch)

    with swiggy_web.browser_page() as opened:
        assert opened is page

    assert page.closed


def test_an_unreachable_chrome_tells_the_host_how_to_start_it(monkeypatch, settings):
    settings.SWIGGY_BOT_CDP_URL = "http://127.0.0.1:9222"
    fake_playwright(monkeypatch, fail=RuntimeError("ECONNREFUSED\nmore"))

    with pytest.raises(HostActionRequired, match="ECONNREFUSED"):  # noqa: SIM117
        with swiggy_web.browser_page():
            pass


def test_without_a_debug_port_a_private_browser_profile_is_used(monkeypatch, settings):
    settings.SWIGGY_BOT_CDP_URL = ""
    page = fake_playwright(monkeypatch)

    with swiggy_web.browser_page() as opened:
        assert opened is page
    existing = fake_playwright(monkeypatch, pages=["first"])
    with swiggy_web.browser_page() as opened:
        assert opened == "first"
    assert existing.closed


def test_a_restaurant_is_opened_by_its_swiggy_id():
    page = Page()

    swiggy_web._open_restaurant(page, "Biryani Life!", "42")

    assert page.visited == [f"{swiggy_web.SWIGGY}/restaurants/biryani-life-42"]


def test_a_transient_error_page_is_retried_then_reported():
    retry = Loc(1)
    page = Page(children={"RETRY": retry, "Uh-oh!": Loc(1)})
    page.count = lambda: 0

    with pytest.raises(HostActionRequired, match="wouldn't open"):
        swiggy_web._open_restaurant(page, "X", "1")

    assert retry.log.count("click") == 3


def card(text):
    return Loc(text=text)


def test_a_restaurant_without_an_id_is_found_by_exact_name_then_clicked():
    cards = Loc(
        3,
        children={
            ("nth", 0): card("Chicking Gourmet"),
            ("nth", 1): card("Chicking"),
            ("nth", 2): card("Other"),
        },
    )
    page = Page(children={swiggy_web.RESTAURANT_CARD: cards})

    swiggy_web._open_restaurant(page, "Chicking", "")

    assert page.visited[0].endswith("/search?query=Chicking")
    assert cards.log == []  # clicks land on the matching card itself
    assert cards.children[("nth", 1)].log == ["click"]


def test_a_partial_name_matches_when_no_exact_name_exists():
    cards = Loc(1, children={("nth", 0): card("Chicking Gourmet Kitchen")})
    page = Page(children={swiggy_web.RESTAURANT_CARD: cards})

    swiggy_web._open_by_search(page, "Chicking")

    assert cards.children[("nth", 0)].log == ["click"]


def test_a_restaurant_missing_from_the_search_is_reported():
    page = Page(children={swiggy_web.RESTAURANT_CARD: Loc(0)})

    with pytest.raises(HostActionRequired, match="Couldn't find"):
        swiggy_web._open_by_search(page, "Nowhere")


def test_searching_for_a_dish_opens_the_search_box_when_it_is_hidden():
    box = Loc(1)
    box.visible = False
    button = Loc(1)
    page = Page(children={swiggy_web.SEARCH_BOX: box, swiggy_web.SEARCH_BUTTON: button})

    swiggy_web._search_dish(page, "Fries")

    assert button.log == ["click"]
    assert box.log == ["fill:", "fill:Fries"]
    assert page.log == ["scroll"]


def test_searching_for_a_dish_reuses_a_visible_box():
    box = Loc(1)
    page = Page(children={swiggy_web.SEARCH_BOX: box})

    swiggy_web._search_dish(page, "Fries")

    assert page.log == []
    assert box.log == ["fill:", "fill:Fries"]


# --- Prompts after Add --------------------------------------------------------------


def test_a_leftover_cart_stops_the_bot_unless_replacing_is_allowed():
    page = Page(children={"Items already in cart": Loc(1)})

    carry_on, detail = swiggy_web._handle_prompts(page, swiggy_web.Policy(), "")

    assert carry_on is False
    assert "another restaurant" in detail


def test_a_leftover_cart_is_replaced_when_allowed():
    reset = Loc(1)
    page = Page(
        children={"Items already in cart": Loc(1), "button": reset},
    )

    carry_on, detail = swiggy_web._handle_prompts(
        page, swiggy_web.Policy(replace_cart=True), ""
    )

    assert (carry_on, detail) == (True, "replaced the old cart")
    assert reset.log == ["click"]


def test_options_to_choose_stop_the_bot_unless_choosing_is_allowed():
    page = Page(children={"dialog": Loc(1)})

    carry_on, detail = swiggy_web._handle_prompts(page, swiggy_web.Policy(), "")

    assert carry_on is False
    assert "AUTO_OPTIONS" in detail


def option_dialog(groups, still_open=False):
    """The 'Customise as per your taste' dialog: groups of (heading, [(label)])."""
    headings = Loc(len(groups) + 1)
    headings.children[("nth", 0)] = Loc(text="Add-ons (optional)")
    clicks = []
    for number, (heading, labels) in enumerate(groups, start=1):
        inputs = Loc(len(labels))
        for i, label in enumerate(labels):
            radio = Loc(label=label)
            radio.children["xpath=following-sibling::span"] = Loc(
                log=clicks, children={}
            )
            inputs.children[("nth", i)] = radio
        sibling = Loc(children={"input[type=radio], input[type=checkbox]": inputs})
        headings.children[("nth", number)] = Loc(
            text=heading, children={"xpath=following-sibling::div[1]": sibling}
        )
    add = Loc(1)
    dialog = Loc(
        1, children={"heading": headings, re.compile("Add Item", re.I).pattern: add}
    )
    page = Page(children={"dialog": dialog if still_open else Loc(0)})
    first = Loc(1, children={"heading": headings, "Add Item": add})
    first.children[re.compile("Add Item", re.I).pattern] = add
    page.children["dialog"] = first
    return page, add, clicks


def test_required_options_are_chosen_then_the_dish_is_added():
    page, add, clicks = option_dialog(
        [("Size 0 out of 1 required", ["Regular, Cost: 0", "Large, Cost: 40"])]
    )
    # The dialog closes once "Add Item" is pressed.
    add.click = lambda: page.children.__setitem__("dialog", Loc(0))

    ok, detail = swiggy_web._choose_options(page, "")

    assert (ok, detail) == (True, "chose Regular")
    assert clicks == ["click"]


def test_a_dish_without_required_options_needs_none_chosen():
    page, add, _ = option_dialog([])
    add.click = lambda: page.children.__setitem__("dialog", Loc(0))

    assert swiggy_web._choose_options(page, "") == (True, "no options needed")


def test_a_group_with_too_few_options_is_left_to_the_host():
    page, _, _ = option_dialog([("Size 0 out of 2 required", ["Only, Cost: 0"])])

    ok, detail = swiggy_web._choose_options(page, "")

    assert ok is False
    assert "more options" in detail


def test_choices_swiggy_does_not_accept_are_reported():
    page, _, _ = option_dialog([("Size 0 out of 1 required", ["Regular, Cost: 0"])])

    ok, detail = swiggy_web._choose_options(page, "")

    assert ok is False
    assert "didn't accept" in detail


def test_prompts_choose_options_when_allowed_and_pass_on_failures():
    page, add, _ = option_dialog([("Size 0 out of 1 required", ["R, Cost: 0"])])
    add.click = lambda: page.children.__setitem__("dialog", Loc(0))
    both = swiggy_web.Policy(auto_options=True)

    assert swiggy_web._handle_prompts(page, both, "") == (True, "chose R")

    stuck, _, _ = option_dialog([("Size 0 out of 2 required", ["R, Cost: 0"])])
    assert swiggy_web._handle_prompts(stuck, both, "")[0] is False
    assert swiggy_web._handle_prompts(Page(), both, "") == (True, "")


# --- Adding a dish ------------------------------------------------------------------


def dish_page(*, add=1, plus=1, matches=1, quantity_prompt=0, body=""):
    item = Loc(
        children={
            swiggy_web.ADD_BUTTON: Loc(add),
            swiggy_web.PLUS_BUTTON: Loc(plus),
        }
    )
    dishes = Loc(matches, children={}, text=body)
    dishes.nth = lambda i: item
    dishes.first = item
    page = Page(
        children={
            swiggy_web.DISH: dishes,
            swiggy_web.SEARCH_BOX: Loc(1),
            "Repeat previous customisation?": Loc(quantity_prompt),
        }
    )
    page.item = item
    return page


def line(quantity=1, name="Fries"):
    return CartLine("Chicking", name, quantity, "", "1")


def test_a_dish_is_added():
    page = dish_page()

    assert swiggy_web._add_line(page, line(), swiggy_web.Policy()) == (
        True,
        "Added 1.",
    )


def test_a_larger_quantity_presses_plus_and_keeps_chosen_options():
    page = dish_page(quantity_prompt=1)

    added, detail = swiggy_web._add_line(page, line(3), swiggy_web.Policy())

    assert added is True
    assert detail == "Added 3."


def test_a_dish_missing_from_the_menu_is_reported():
    added, detail = swiggy_web._add_line(
        dish_page(matches=0), line(), swiggy_web.Policy()
    )

    assert added is False
    assert detail.startswith("Not found")


def test_a_sold_out_dish_is_reported():
    added, detail = swiggy_web._add_line(
        dish_page(add=0, plus=0), line(), swiggy_web.Policy()
    )

    assert added is False
    assert "Unavailable" in detail


def test_a_prompt_that_cannot_be_answered_stops_the_dish():
    page = dish_page()
    page.children["Items already in cart"] = Loc(1)

    added, detail = swiggy_web._add_line(page, line(), swiggy_web.Policy())

    assert added is False
    assert "another restaurant" in detail


def test_a_missing_plus_button_is_reported_with_the_wanted_quantity():
    page = dish_page(plus=0)

    added, detail = swiggy_web._add_line(page, line(2), swiggy_web.Policy())

    assert added is False
    assert "(wanted 2)" in detail


def test_a_dish_already_in_the_cart_is_left_alone_when_the_amount_is_enough(
    monkeypatch,
):
    page = dish_page(add=0)
    monkeypatch.setattr(swiggy_web, "_checkout_text", lambda p: "Fries + 2 | 99")

    added, detail = swiggy_web._add_line(page, line(2), swiggy_web.Policy())

    assert (added, detail) == (True, "Already in the cart (2), left as it is.")


def test_a_dish_already_in_the_cart_is_topped_up(monkeypatch):
    page = dish_page(add=0)
    monkeypatch.setattr(swiggy_web, "_checkout_text", lambda p: "Fries + 1 | 99")

    added, detail = swiggy_web._add_line(page, line(3), swiggy_web.Policy())

    assert (added, detail) == (True, "Was in the cart 1; raised to 3.")


def test_a_top_up_that_cannot_press_plus_is_reported(monkeypatch):
    page = dish_page(add=0, plus=0)
    item = page.item
    item.children[swiggy_web.PLUS_BUTTON] = Loc(0)
    monkeypatch.setattr(swiggy_web, "_checkout_text", lambda p: "Fries + 1 | 99")

    added, detail = swiggy_web._top_up(page, item, line(3))

    assert added is False
    assert "In the cart 1 of 3" in detail


def test_an_unreadable_cart_quantity_leaves_the_dish_as_it_is(monkeypatch):
    page = dish_page(add=0)
    monkeypatch.setattr(swiggy_web, "_checkout_text", lambda p: "nothing here")

    added, detail = swiggy_web._top_up(page, page.item, line(3))

    assert added is True
    assert "couldn't read" in detail


def test_the_checkout_text_is_read_in_a_spare_tab():
    page = Page(children={"body": Loc(text="Fries + 2 ")})

    assert swiggy_web._checkout_text(page) == "Fries + 2 "
    assert page.closed


# --- Verifying and filling the cart -------------------------------------------------


def checkout(text):
    return Page(children={"body": Loc(text=text)})


def test_a_cart_with_every_dish_in_the_right_amount_passes():
    lines = (line(2), line(1, "Zinger Burger"))
    page = checkout("Fries + 2 | 99 Zinger Burger + 1 | 99")

    swiggy_web._verify_cart(page, lines)

    assert page.visited == [f"{swiggy_web.SWIGGY}/checkout"]


def test_a_cart_missing_a_dish_or_with_the_wrong_amount_is_refused():
    lines = (line(2), line(1, "Zinger Burger"), line(1, "Cola"))
    page = checkout("Fries + 1 | 99 Zinger Burger + 1 | 99")

    with pytest.raises(HostActionRequired) as problem:
        swiggy_web._verify_cart(page, lines)

    assert "Fries: cart has 1, order wants 2" in str(problem.value)
    assert "Cola isn't in the cart" in str(problem.value)


def fill_page(body):
    item = Loc(children={swiggy_web.ADD_BUTTON: Loc(1), swiggy_web.PLUS_BUTTON: Loc(1)})
    dishes = Loc(1)
    dishes.first = item
    page = Page(
        children={
            swiggy_web.DISH: dishes,
            swiggy_web.SEARCH_BOX: Loc(1),
            "body": Loc(text=body),
        }
    )
    return page


def test_filling_the_cart_adds_every_dish_then_checks_it():
    lines = (line(1), line(2, "Zinger Burger"))
    page = fill_page("Fries + 1 | 9 Zinger Burger + 2 | 99")
    progress = Progress()
    progress.plan(swiggy_web.plan_steps(lines))

    swiggy_web.fill_cart(page, lines, require_login=True, progress=progress)

    done = {
        key: status
        for key, status in statuses(progress).items()
        if key not in ("browser", "handoff")
    }
    assert set(done.values()) == {"done"}
    assert progress.steps[2]["key"] == "open:Chicking"


def test_a_dish_that_cannot_be_added_fails_its_step_and_hands_over(monkeypatch):
    lines = (line(1), line(1, "Zinger Burger"))
    page = fill_page("")
    results = iter([(True, "ok"), (False, "Sold out")])
    monkeypatch.setattr(swiggy_web, "_add_line", lambda *a: next(results))
    progress = Progress()
    progress.plan(swiggy_web.plan_steps(lines))

    with pytest.raises(HostActionRequired, match="only partly filled"):
        swiggy_web.fill_cart(
            page,
            lines,
            require_login=False,
            progress=progress,
            policy=swiggy_web.Policy(replace_cart=True),
        )

    assert statuses(progress)["add:Chicking:Zinger Burger"] == "failed"
    assert statuses(progress)["verify"] == "pending"


def test_only_the_first_restaurant_may_replace_a_leftover_cart(monkeypatch):
    policies = []
    monkeypatch.setattr(swiggy_web, "_open_restaurant", lambda *a: None)
    monkeypatch.setattr(swiggy_web, "_verify_cart", lambda *a: None)

    def add(page, line, policy):
        policies.append(policy.replace_cart)
        return True, "ok"

    monkeypatch.setattr(swiggy_web, "_add_line", add)
    lines = (CartLine("A", "x", 1, ""), CartLine("B", "y", 1, ""))
    progress = Progress()
    progress.plan(swiggy_web.plan_steps(lines))

    swiggy_web.fill_cart(
        Page(),
        lines,
        require_login=False,
        progress=progress,
        policy=swiggy_web.Policy(replace_cart=True),
    )

    assert policies == [True, False]
