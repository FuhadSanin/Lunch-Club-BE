"""Local menus: a host's own list of a restaurant that isn't on Swiggy, with its dishes
and prices. See `LocalMenu`."""

import re
import uuid
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from rest_framework import serializers

from apps.lunches.models import LocalMenu

LOCAL_PREFIX = "local-"
MAX_MENUS = 30
MAX_ITEMS = 300
# A picture is a link, or a small one kept in the row. Sizes are in characters.
MAX_RESTAURANT_IMAGE = 80_000
MAX_ITEM_IMAGE = 30_000
MAX_PRICE_PAISE = 5_000_000
DATA_IMAGE = re.compile(r"^data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$")


def is_local(restaurant_id: str) -> bool:
    return restaurant_id.startswith(LOCAL_PREFIX)


def _image(value: str, limit: int) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("data:"):
        if not DATA_IMAGE.match(value):
            raise serializers.ValidationError("Use a PNG, JPEG or WebP picture.")
        if len(value) > limit:
            raise serializers.ValidationError(
                "That picture is too big. Use a smaller one."
            )
        return value
    try:
        URLValidator(schemes=["https"])(value)
    except DjangoValidationError as error:
        raise serializers.ValidationError(
            "Use a picture link that starts with https://."
        ) from error
    if len(value) > 1000:
        raise serializers.ValidationError("That picture link is too long.")
    return value


class LocalItemSerializer(serializers.Serializer):
    id = serializers.CharField(max_length=40, required=False)
    name = serializers.CharField(max_length=200, trim_whitespace=True)
    price_paise = serializers.IntegerField(min_value=1, max_value=MAX_PRICE_PAISE)
    image = serializers.CharField(required=False, allow_blank=True, default="")
    veg = serializers.BooleanField(required=False, allow_null=True, default=None)

    def validate_image(self, value: str) -> str:
        return _image(value, MAX_ITEM_IMAGE)


class LocalMenuSerializer(serializers.ModelSerializer):
    name = serializers.CharField(max_length=120, trim_whitespace=True)
    image = serializers.CharField(required=False, allow_blank=True, default="")
    rating = serializers.FloatField(
        min_value=0, max_value=5, required=False, allow_null=True
    )
    distance_km = serializers.FloatField(
        min_value=0, max_value=200, required=False, allow_null=True
    )
    # many=True builds a ListSerializer, which takes these; the stubs don't know.
    items = LocalItemSerializer(  # type: ignore[call-arg]
        many=True, allow_empty=False, max_length=MAX_ITEMS
    )

    class Meta:
        model = LocalMenu
        fields = [
            "id",
            "name",
            "image",
            "rating",
            "distance_km",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_image(self, value: str) -> str:
        return _image(value, MAX_RESTAURANT_IMAGE)

    def validate_items(self, items: list[dict]) -> list[dict]:
        out, seen = [], set()
        for item in items:
            key = item["name"].strip().lower()
            if key in seen:
                raise serializers.ValidationError(
                    f"“{item['name']}” is on the menu twice."
                )
            seen.add(key)
            out.append({**item, "id": item.get("id") or uuid.uuid4().hex[:12]})
        return out


def local_offered(owner, ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    """The owner's menus `ids` as restaurants for a party's list, dishes included.
    Raises a ValidationError if one isn't theirs."""
    wanted = list(dict.fromkeys(ids))
    found = {m.id: m for m in LocalMenu.objects.filter(owner=owner, id__in=wanted)}
    if len(found) != len(wanted):
        raise serializers.ValidationError(
            {"local_menu_ids": ["One of those local menus isn't yours any more."]}
        )
    return [offered_from(found[i]) for i in wanted]


def offered_from(menu: LocalMenu) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": f"{LOCAL_PREFIX}{menu.id}",
        "name": menu.name,
        "local": True,
        "items": [
            {
                "id": item["id"],
                "name": item["name"],
                "price_paise": item["price_paise"],
                "photo": item.get("image") or "",
                "veg": item.get("veg"),
            }
            for item in menu.items
        ],
    }
    if menu.image:
        entry["photo"] = menu.image
    if menu.rating is not None:
        entry["rating"] = menu.rating
    if menu.distance_km is not None:
        entry["distance_km"] = menu.distance_km
    return entry
