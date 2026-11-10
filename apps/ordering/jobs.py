from apps.core.jobs import job
from apps.ordering import services


@job("place-order")
def place_order(payload: dict) -> None:
    services.place(payload["org_id"], payload["order_id"])


@job("poll-delivery")
def poll_delivery(payload: dict) -> None:
    services.poll_delivery(payload["org_id"], payload["order_id"], payload["poll"])
