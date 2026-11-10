from apps.ordering.progress import Progress
from apps.ordering.providers.base import (
    HostActionRequired,
    OrderProvider,
    PlaceRequest,
    PlaceResult,
)


class ManualProvider(OrderProvider):
    """The host checks out in the Swiggy app and enters the order number in our app.

    This is also the fallback whenever automatic ordering fails.
    """

    def place(self, request: PlaceRequest, progress: Progress) -> PlaceResult:
        raise HostActionRequired(
            "Place this order in the Swiggy app, then enter its Swiggy order number "
            "here."
        )

    def find_existing(self, request: PlaceRequest) -> PlaceResult | None:
        return None
