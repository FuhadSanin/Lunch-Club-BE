"""Drives swiggy.com in a browser to fill the host's cart (Playwright, sync API).

Every step was checked against the live site. Swiggy renders only the first menu
category and collapses the rest, so dishes are found with the restaurant's own
"Search for dishes" box instead of by scrolling. Adding is idempotent: a dish already
in the cart is left alone, so a retry never doubles quantities.

Nothing here checks out or pays. Problems the bot must not guess its way around raise
HostActionRequired, which hands the order to the host.
"""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from urllib.parse import quote

from django.conf import settings

from apps.ordering.progress import Progress
from apps.ordering.providers.base import CartLine, HostActionRequired

logger = logging.getLogger(__name__)

SWIGGY = "https://www.swiggy.com"
DISH = '[data-testid="normal-dish-item"]:visible'
ADD_BUTTON = "button.add-button-center-container"
PLUS_BUTTON = "button.add-button-right-container"
RESTAURANT_CARD = '[data-testid="resturant-card-name"]'
SEARCH_BOX = 'input[data-cy="menu-search-header"]'
SEARCH_BUTTON = '[data-cy="menu-search-button"]:visible'


@dataclass(frozen=True)
class Policy:
    """What the bot may do when Swiggy asks a question, instead of stopping."""

    # "Your cart has items from another restaurant. Reset it?" -> "Yes, start afresh".
    replace_cart: bool = False
    # A customisable dish: choose its required options (see pick_options).
    auto_options: bool = False


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


ADD_WHY = (
    "Searches the restaurant's menu for the dish and presses Add. A dish already in "
    "the cart is left alone, so retrying never doubles it."
)


def plan_steps(lines: tuple[CartLine, ...]) -> list[tuple[str, str, str]]:
    """Every checkpoint of a placement, in order, with the reason for each."""
    steps = [
        (
            "browser",
            "Connect to your Swiggy browser",
            "The bot works in your own logged-in Chrome, so the cart ends up on your "
            "Swiggy account.",
        ),
        (
            "login",
            "Check you're signed in to Swiggy",
            "A cart built while signed out isn't on your account, so you couldn't "
            "check it out.",
        ),
    ]
    for restaurant, group in _by_restaurant(lines):
        steps.append(
            (
                f"open:{restaurant}",
                f"Open {restaurant}",
                "Swiggy shows one restaurant's menu at a time; the dishes are added "
                "from it.",
            )
        )
        steps += [
            (add_key(line), f"Add {line.quantity}× {line.dish_name}", ADD_WHY)
            for line in group
        ]
    steps += [
        (
            "verify",
            "Check the cart",
            "Opens Swiggy's checkout page and confirms every dish is really in the "
            "cart before handing over.",
        ),
        (
            "handoff",
            "Hand over to you",
            "The bot never pays. You check out in the Swiggy app, then enter the "
            "order number here.",
        ),
    ]
    return steps


def add_key(line: CartLine) -> str:
    return f"add:{line.restaurant_name}:{line.dish_name}"


@contextmanager
def browser_page() -> Iterator:
    """A page in the host's logged-in browser; imported lazily so the API image
    doesn't need Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise HostActionRequired(
            "The order bot needs Playwright, which this API doesn't have (it is "
            "probably running in Docker). Run the API with `make bot-api`, then press "
            "Retry."
        ) from error

    with sync_playwright() as playwright:
        if settings.SWIGGY_BOT_CDP_URL:
            url = settings.SWIGGY_BOT_CDP_URL
            try:
                browser = playwright.chromium.connect_over_cdp(url)
            except Exception as error:
                lines = str(error).strip().splitlines()
                reason = lines[0] if lines else type(error).__name__
                raise HostActionRequired(
                    f"Can't reach Chrome at {url}. Quit Chrome (Cmd+Q), start it with "
                    "--remote-debugging-port=9222 (see README: Order bot), log in to "
                    "Swiggy in that window, then press Retry. "
                    f"({reason})"
                ) from error
            page = browser.contexts[0].new_page()  # never touches the host's own tabs
            try:
                yield page
            finally:
                page.close()
        else:
            context = playwright.chromium.launch_persistent_context(
                settings.SWIGGY_BOT_PROFILE_DIR,
                headless=settings.SWIGGY_BOT_HEADLESS,
                viewport={"width": 1366, "height": 900},
            )
            try:
                yield context.pages[0] if context.pages else context.new_page()
            finally:
                context.close()


def fill_cart(
    page,
    lines: tuple[CartLine, ...],
    *,
    require_login: bool,
    progress: Progress,
    policy: Policy = Policy(),  # noqa: B008
) -> None:
    """Put every line in the Swiggy cart and check it is there, or raise
    HostActionRequired saying what is wrong."""
    with progress.step("login"):
        _check_logged_in(page, require_login)
        if not require_login:
            progress.done(
                "login",
                "Skipped: SWIGGY_BOT_REQUIRE_LOGIN is off, so the cart is built in "
                "this browser's Swiggy session (a guest cart if signed out).",
            )
    problems: list[str] = []
    for number, (restaurant, group) in enumerate(_by_restaurant(lines)):
        # Only the first restaurant may clear a leftover cart; replacing it for a later
        # restaurant would throw away the dishes just added for the earlier one.
        here = policy if number == 0 else replace(policy, replace_cart=False)
        with progress.step(f"open:{restaurant}"):
            _open_restaurant(page, restaurant, group[0].restaurant_id)
        for line in group:
            key = add_key(line)
            with progress.step(key):
                added, detail = _add_line(page, line, here)
                logger.info(
                    "Swiggy cart line",
                    extra={"fields": {"dish": line.dish_name, "detail": detail}},
                )
                if added:
                    progress.done(key, detail)
                else:
                    progress.fail(key, detail)
                    problems.append(f"{line.dish_name} ({restaurant}): {detail}")
    if problems:
        raise HostActionRequired(
            "The Swiggy cart is only partly filled. Fix these in the Swiggy app, then "
            "check out: " + "; ".join(problems) + "."
        )
    with progress.step("verify"):
        _verify_cart(page, lines)


def _by_restaurant(lines):
    groups: dict[str, list[CartLine]] = {}
    for line in lines:
        groups.setdefault(line.restaurant_name, []).append(line)
    return groups.items()


def _check_logged_in(page, require_login: bool) -> None:
    page.goto(SWIGGY, timeout=60000)
    page.wait_for_timeout(3000)
    if require_login and page.get_by_text("Sign In", exact=True).count():
        raise HostActionRequired(
            "Swiggy is signed out for the order bot. Log in to Swiggy again, or place "
            "this order in the Swiggy app."
        )


def _open_restaurant(page, name: str, restaurant_id: str) -> None:
    if restaurant_id:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        page.goto(f"{SWIGGY}/restaurants/{slug}-{restaurant_id}", timeout=60000)
        page.wait_for_timeout(3500)
    else:
        _open_by_search(page, name)
    for _ in range(3):  # Swiggy sometimes shows a transient "Uh-oh" page
        retry = page.get_by_text("RETRY", exact=True)
        if not retry.count():
            break
        retry.first.click()
        page.wait_for_timeout(4000)
    if page.get_by_text("Uh-oh!").count():
        raise HostActionRequired(f"Swiggy wouldn't open {name}. Try again in the app.")


def _open_by_search(page, name: str) -> None:
    page.goto(f"{SWIGGY}/search?query={quote(name)}", timeout=60000)
    page.wait_for_timeout(4000)
    cards = page.locator(RESTAURANT_CARD)
    names = [
        " ".join(cards.nth(i).inner_text().split())
        for i in range(min(cards.count(), 20))
    ]
    want = norm(name)
    # An exact name beats a partial one ("McDonald's" vs "McDonald's Gourmet ...").
    matches = [i for i, n in enumerate(names) if norm(n) == want] or [
        i for i, n in enumerate(names) if want in norm(n)
    ]
    if not matches:
        raise HostActionRequired(f"Couldn't find {name} on Swiggy.")
    cards.nth(matches[0]).click()
    page.wait_for_timeout(4000)


def _search_dish(page, dish: str) -> None:
    box = page.locator(SEARCH_BOX)
    if not box.count() or not box.first.is_visible():
        page.evaluate("window.scrollTo(0, 0)")
        page.locator(SEARCH_BUTTON).first.click()
        box.first.wait_for(state="visible", timeout=10000)
    box.first.fill("")
    box.first.fill(dish)
    page.wait_for_timeout(2500)


def pick_options(
    options: list[tuple[str, int]], need: int, note: str
) -> list[str] | None:
    """Which options to choose for one required group, as names (None if the group
    has too few). An option named in the order's note wins; otherwise the cheapest,
    which is normally the +Rs 0 default. `options` are (name, extra price in rupees)."""
    wanted = note.lower()
    ranked = sorted(
        range(len(options)),
        key=lambda i: (options[i][0].lower() not in wanted, options[i][1], i),
    )
    if len(options) < need:
        return None
    return [options[i][0] for i in ranked[:need]]


def _choose_options(page, note: str) -> tuple[bool, str]:
    """Fill the "Customise as per your taste" dialog's required groups, then add."""
    dialog = page.get_by_role("dialog").first
    chosen: list[str] = []
    headings = dialog.get_by_role("heading")
    for number in range(headings.count()):
        heading = headings.nth(number)
        found = re.search(r"(\d+) out of (\d+) required", heading.text_content() or "")
        if not found:  # an optional group (a beverage, a dessert): leave it
            continue
        need = int(found.group(2)) - int(found.group(1))
        inputs = heading.locator("xpath=following-sibling::div[1]").locator(
            "input[type=radio], input[type=checkbox]"
        )
        options = []
        for i in range(inputs.count()):
            label = inputs.nth(i).get_attribute("aria-label") or ""
            cost = re.search(r"Cost:\s*(\d+)", label)
            options.append(
                (label.split(",")[0].strip(), int(cost.group(1)) if cost else 0)
            )
        picked = pick_options(options, need, note)
        if picked is None:
            return (
                False,
                "Swiggy needs more options than it showed. Choose them in Swiggy.",
            )
        for name in picked:
            box = inputs.nth([o[0] for o in options].index(name))
            box.locator("xpath=following-sibling::span").first.click()
        chosen += picked
    dialog.get_by_text(re.compile("Add Item", re.I)).last.click()
    page.wait_for_timeout(2500)
    if page.get_by_role("dialog").count():
        return False, "Swiggy didn't accept the choices. Choose the options in Swiggy."
    return True, "chose " + "; ".join(chosen) if chosen else "no options needed"


def _handle_prompts(page, policy: Policy, note: str) -> tuple[bool, str]:
    """Answer what Swiggy asks after Add: (carry on?, what happened or the problem)."""
    done: list[str] = []
    if page.get_by_text("Items already in cart").count():
        # Swiggy: "Your cart contains items from other restaurant. Reset your cart?"
        if not policy.replace_cart:
            return False, (
                "Swiggy's cart holds another restaurant's items. Clear it in Swiggy, "
                "or turn on SWIGGY_BOT_REPLACE_CART (it only applies to the order's "
                "first restaurant)."
            )
        page.get_by_role("button", name=re.compile("start afresh", re.I)).click()
        page.wait_for_timeout(2500)
        done.append("replaced the old cart")
    if page.get_by_role("dialog").count():  # "Customise as per your taste"
        if not policy.auto_options:
            return False, (
                "This dish has options to choose. Choose them in Swiggy, or turn on "
                "SWIGGY_BOT_AUTO_OPTIONS."
            )
        chosen, detail = _choose_options(page, note)
        if not chosen:
            return False, detail
        done.append(detail)
    return True, ", ".join(done)


def cart_quantity(page_text: str, dish: str) -> int | None:
    """How many of `dish` the checkout page shows, e.g. "...each | Customize | + | 3 |
    1134" -> 3. None if the dish isn't listed or the layout isn't recognised."""
    flat = " ".join(page_text.split())
    found = re.search(
        re.escape(" ".join(dish.split())) + r"\W*(?:Customi[sz]e\W*)?\+?\W*(\d{1,2})\s",
        flat,
        re.I,
    )
    return int(found.group(1)) if found else None


def _checkout_text(page) -> str:
    """The checkout page's text, read in a spare tab so the menu tab stays put."""
    spare = page.context.new_page()
    try:
        spare.goto(f"{SWIGGY}/checkout", timeout=60000)
        spare.wait_for_timeout(4500)
        return spare.locator("body").inner_text()
    finally:
        spare.close()


def _raise_quantity(page, item, times: int) -> str:
    """Press + `times` times. Swiggy asks "Repeat previous customisation?" for a
    customisable dish; Repeat keeps the options already chosen. Returns a problem or
    ""."""
    plus = item.locator(PLUS_BUTTON)
    for _ in range(times):
        if not plus.count():
            return "couldn't find the + button"
        plus.first.click()
        page.wait_for_timeout(1200)
        if page.get_by_text("Repeat previous customisation?").count():
            page.get_by_role("button", name="Repeat", exact=True).click()
            page.wait_for_timeout(1500)
    return ""


def _add_line(page, line: CartLine, policy: Policy) -> tuple[bool, str]:
    """Add one dish: (added?, what happened or why not)."""
    _search_dish(page, line.dish_name)
    matches = page.locator(DISH).filter(
        has=page.get_by_text(line.dish_name, exact=True)
    )
    if not matches.count():
        return False, "Not found on the restaurant's menu."
    item = matches.first
    add = item.locator(ADD_BUTTON)
    if not add.count():
        if item.locator(PLUS_BUTTON).count():
            return _top_up(page, item, line)
        return False, "Unavailable: the dish has no Add button (sold out?)."
    add.first.click()
    page.wait_for_timeout(1800)
    carry_on, detail = _handle_prompts(page, policy, line.note)
    if not carry_on:
        return False, detail
    problem = _raise_quantity(page, item, line.quantity - 1)
    if problem:
        return False, f"Added once, but {problem} (wanted {line.quantity})."
    return True, f"Added {line.quantity}" + (f" ({detail})." if detail else ".")


def _top_up(page, item, line: CartLine) -> tuple[bool, str]:
    """The dish is already in the cart (an earlier attempt): add only what's missing."""
    have = cart_quantity(_checkout_text(page), line.dish_name)
    if have is None:
        return True, "Already in the cart, left as it is (couldn't read its quantity)."
    if have >= line.quantity:
        return True, f"Already in the cart ({have}), left as it is."
    problem = _raise_quantity(page, item, line.quantity - have)
    if problem:
        return False, f"In the cart {have} of {line.quantity}, but {problem}."
    return True, f"Was in the cart {have}; raised to {line.quantity}."


def _verify_cart(page, lines: tuple[CartLine, ...]) -> None:
    page.goto(f"{SWIGGY}/checkout", timeout=60000)
    page.wait_for_timeout(5000)
    text = page.locator("body").inner_text()
    flat = norm(text)
    problems = []
    for line in lines:
        if norm(line.dish_name) not in flat:
            problems.append(f"{line.dish_name} isn't in the cart")
            continue
        have = cart_quantity(text, line.dish_name)
        if have is not None and have != line.quantity:
            problems.append(
                f"{line.dish_name}: cart has {have}, order wants {line.quantity}"
            )
    if problems:
        raise HostActionRequired(
            "The Swiggy cart doesn't match: " + "; ".join(problems) + "."
        )
