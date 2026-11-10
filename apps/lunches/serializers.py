from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from apps.accounts.serializers import UserSerializer
from apps.lunches.checkout import checkout_carts
from apps.lunches.models import OrderItem, OrderSession, SwiggyOrder

MAX_LEAD_TIME = timedelta(hours=24)


class PersonSerializer(UserSerializer):
    class Meta(UserSerializer.Meta):
        fields = ["id", "name"]
        read_only_fields = fields


class StartLunchSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderSession
        fields = ["cutoff_at", "delivery_address"]

    def validate_cutoff_at(self, value):
        now = timezone.now()
        if not now < value <= now + MAX_LEAD_TIME:
            raise serializers.ValidationError("Pick a cutoff within the next 24 hours.")
        return value


class ItemSerializer(serializers.ModelSerializer):
    user = PersonSerializer(read_only=True)
    restaurant_name = serializers.CharField(max_length=200, trim_whitespace=True)
    restaurant_id = serializers.RegexField(
        r"^\d{1,20}$", allow_blank=True, required=False, default=""
    )
    dish_name = serializers.CharField(max_length=200, trim_whitespace=True)
    quantity = serializers.IntegerField(min_value=1, max_value=20)
    note = serializers.CharField(max_length=200, allow_blank=True, required=False)

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "user",
            "restaurant_name",
            "restaurant_id",
            "dish_name",
            "quantity",
            "price_paise",
            "note",
        ]
        read_only_fields = ["id", "user"]


class UnlockSerializer(serializers.Serializer):
    """POST /api/lunches/<id>/unlock: optionally, a new cutoff within 24 hours."""

    cutoff_at = serializers.DateTimeField(required=False)

    def validate_cutoff_at(self, value):
        return StartLunchSerializer().validate_cutoff_at(value)


class PicksSerializer(serializers.Serializer):
    """PUT /api/lunches/<id>/picks: your whole cart, and whether it's complete."""

    # many=True builds a ListSerializer, which takes max_length; the stubs don't know.
    items = ItemSerializer(many=True, max_length=50)  # type: ignore[call-arg]
    complete = serializers.BooleanField(default=False)


class SwiggyOrderSerializer(serializers.ModelSerializer):
    restaurants = serializers.SerializerMethodField()
    carts = serializers.SerializerMethodField()

    class Meta:
        model = SwiggyOrder
        fields = [
            "id",
            "status",
            "swiggy_order_id",
            "restaurants",
            "carts",
            "restaurant_count",
            "total_paise",
            "placed_at",
            "last_error",
            "bot_steps",
        ]
        read_only_fields = fields

    def get_restaurants(self, order: SwiggyOrder) -> list[str]:
        return sorted({item.restaurant_name for item in order.items.all()})

    def get_carts(self, order: SwiggyOrder) -> list[dict]:
        return checkout_carts(order.items.all())


class LunchSerializer(serializers.ModelSerializer):
    host = PersonSerializer(read_only=True)

    class Meta:
        model = OrderSession
        fields = ["id", "host", "cutoff_at", "status", "created_at"]
        read_only_fields = fields


class LunchDetailSerializer(LunchSerializer):
    """Everything on the live board. Expects `lunches.queries.with_board` prefetch."""

    items = ItemSerializer(many=True, read_only=True)
    orders = SwiggyOrderSerializer(many=True, read_only=True)
    skipped = PersonSerializer(source="skipped_by", many=True, read_only=True)
    completed = PersonSerializer(source="completed_by", many=True, read_only=True)
    total_paise = serializers.SerializerMethodField()

    class Meta(LunchSerializer.Meta):
        fields = [
            *LunchSerializer.Meta.fields,
            "delivery_address",
            "items",
            "skipped",
            "completed",
            "orders",
            "total_paise",
        ]
        read_only_fields = fields

    def get_total_paise(self, lunch: OrderSession) -> int:
        return sum(item.total_paise for item in lunch.items.all())
