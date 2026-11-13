from apps.ordering.progress import Progress
from apps.ordering.providers.base import (
    HostActionRequired,
    OrderProvider,
    PlaceRequest,
    PlaceResult,
)


class ManualProvider(OrderProvider):
    """The host checks out in the Swiggy app and enters the order number in our app.

    This is the provider in use until Swiggy approves automatic ordering (the order bot
    in `swiggy_web.py` is built but switched off), and the fallback whenever automatic
    ordering fails.
    """

    def place(self, request: PlaceRequest, progress: Progress) -> PlaceResult:
        raise HostActionRequired(
            "Automatic ordering is waiting for Swiggy approval, so place this order "
            "yourself in the Swiggy app, then mark it as ordered here (you can add "
            "its Swiggy order number)."
        )

    def find_existing(self, request: PlaceRequest) -> PlaceResult | None:
        return None
