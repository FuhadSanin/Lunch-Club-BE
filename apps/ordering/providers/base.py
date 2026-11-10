"""The contract every way of placing a Swiggy order fulfils.

Today that is the manual provider (the host checks out in the Swiggy app). The order bot
and, once Swiggy approves access, the Swiggy MCP plug in here without other changes;
pick one with the ORDER_PROVIDER setting.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from apps.ordering.progress import Progress


@dataclass(frozen=True)
class CartLine:
    restaurant_name: str
    dish_name: str
    quantity: int
    note: str
    # Swiggy's id for the restaurant; empty if unknown (the bot then searches by name).
    restaurant_id: str = ""


@dataclass(frozen=True)
class PlaceRequest:
    # Unique per checkout and stable across retries: the one-time key against
    # double orders.
    order_id: UUID
    host_id: UUID
    delivery_address: str
    lines: tuple[CartLine, ...]
    total_paise: int


@dataclass(frozen=True)
class PlaceResult:
    swiggy_order_id: str


class HostActionRequired(Exception):
    """This order can't be placed automatically; the message tells the host why."""


class OrderProvider(ABC):
    # Whether `is_delivered` works; if not, the host marks lunches as delivered.
    tracks_delivery = False
    # Whether this provider's orders must go one at a time: the next starts only once
    # the host has checked out the previous one (the Swiggy cart holds one at a time).
    sequential = False

    @abstractmethod
    def place(self, request: PlaceRequest, progress: "Progress") -> PlaceResult:
        """Check out once on Swiggy, or raise HostActionRequired.

        Providers that work in steps announce them on `progress` so the host can follow.
        """

    @abstractmethod
    def find_existing(self, request: PlaceRequest) -> PlaceResult | None:
        """Return the order if an earlier, interrupted attempt already placed it."""

    def is_delivered(self, swiggy_order_id: str) -> bool:
        raise NotImplementedError
