from contextlib import ExitStack

from django.conf import settings

from apps.ordering import swiggy_web
from apps.ordering.progress import Progress
from apps.ordering.providers.base import (
    HostActionRequired,
    OrderProvider,
    PlaceRequest,
    PlaceResult,
)


class SwiggyWebProvider(OrderProvider):
    """Fills the host's Swiggy cart on the website, then hands over for payment.

    The cart lives on the host's Swiggy account, so the host finishes in the Swiggy app
    and enters the order number, exactly as with the manual provider. Checking out and
    paying automatically is the remaining part of the order-bot change
    (openspec/changes/add-swiggy-order-bot) and needs a supervised run on a real order.

    Safe to run again: dishes already in the cart are left alone.
    """

    # One Swiggy cart at a time: restaurant 2 waits until restaurant 1 is ordered.
    sequential = True

    def place(self, request: PlaceRequest, progress: Progress) -> PlaceResult:
        progress.plan(swiggy_web.plan_steps(request.lines))
        with ExitStack() as stack:
            with progress.step("browser"):
                page = stack.enter_context(swiggy_web.browser_page())
            swiggy_web.fill_cart(
                page,
                request.lines,
                require_login=settings.SWIGGY_BOT_REQUIRE_LOGIN,
                policy=swiggy_web.Policy(
                    replace_cart=settings.SWIGGY_BOT_REPLACE_CART,
                    auto_options=settings.SWIGGY_BOT_AUTO_OPTIONS,
                ),
                progress=progress,
            )
        progress.done("handoff", "Your cart is ready in Swiggy")
        raise HostActionRequired(
            "Your Swiggy cart is ready with this order. Open the Swiggy app, check "
            "out, then enter the Swiggy order number here."
        )

    def find_existing(self, request: PlaceRequest) -> PlaceResult | None:
        return None  # the bot never places an order, so there is nothing to find
