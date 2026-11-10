from apps.ordering.progress import Progress
from apps.ordering.providers.base import OrderProvider, PlaceRequest, PlaceResult


class FakeProvider(OrderProvider):
    """Records calls; tests set `existing` or `error` to steer it."""

    tracks_delivery = True

    def __init__(self):
        self.calls = []
        self.existing = None
        self.error = None

    def place(self, request: PlaceRequest, progress: Progress) -> PlaceResult:
        self.calls.append("place")
        if self.error:
            raise self.error
        return PlaceResult(swiggy_order_id=f"SW-{request.order_id.hex[:8]}")

    def find_existing(self, request: PlaceRequest) -> PlaceResult | None:
        self.calls.append("find_existing")
        return self.existing

    def is_delivered(self, swiggy_order_id: str) -> bool:
        self.calls.append("is_delivered")
        return True
