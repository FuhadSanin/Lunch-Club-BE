from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from apps.accounts.serializers import UserSerializer
from apps.core import tenancy
from apps.lunches.checkout import checkout_carts
from apps.lunches.models import OrderItem, OrderSession, SessionMember, SwiggyOrder

MAX_LEAD_TIME = timedelta(hours=24)


class PersonSerializer(UserSerializer):
    class Meta(UserSerializer.Meta):
        fields = ["id", "name"]
        read_only_fields = fields


class OfferedRestaurantSerializer(serializers.Serializer):
    """One restaurant on a lunch's list, as Swiggy showed it to the host."""

    id = serializers.CharField(max_length=40)
    name = serializers.CharField(max_length=200)
    area = serializers.CharField(max_length=200, required=False, allow_blank=True)
    cuisines = serializers.ListField(
        child=serializers.CharField(max_length=60), max_length=20, required=False
    )
    rating = serializers.FloatField(min_value=0, max_value=5, required=False)
    eta = serializers.CharField(max_length=40, required=False, allow_blank=True)
    distance_km = serializers.FloatField(min_value=0, max_value=100, required=False)
    photo = serializers.URLField(max_length=500, required=False, allow_blank=True)


class StartLunchSerializer(serializers.ModelSerializer):
    name = serializers.CharField(
        max_length=80, trim_whitespace=True, allow_blank=True, required=False
    )
    # many=True builds a ListSerializer, which takes max_length; the stubs don't know.
    offered_restaurants = OfferedRestaurantSerializer(  # type: ignore[call-arg]
        many=True, required=False, max_length=100
    )

    class Meta:
        model = OrderSession
        fields = [
            "name",
            "cutoff_at",
            "delivery_address",
            "delivery_lat",
            "delivery_lng",
            "offered_restaurants",
        ]

    def validate(self, attrs):
        if (attrs.get("delivery_lat") is None) != (attrs.get("delivery_lng") is None):
            raise serializers.ValidationError(
                {"delivery_lat": "Give the address's latitude and longitude together."}
            )
        return attrs

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
    """Pass `context={"user": the caller}` to fill in `joined`."""

    host = PersonSerializer(read_only=True)
    org_kind = serializers.CharField(source="org.kind", read_only=True)
    joined = serializers.SerializerMethodField()

    class Meta:
        model = OrderSession
        fields = [
            "id",
            "name",
            "host",
            "org_kind",
            "joined",
            "cutoff_at",
            "status",
            "delivery_address",
            "delivery_lat",
            "delivery_lng",
            "offered_restaurants",
            "closed_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_joined(self, lunch: OrderSession) -> bool:
        """The caller takes part from outside the lunch's org."""
        user = self.context.get("user")
        return user is not None and lunch.org_id != user.org_id


class ContactSerializer(UserSerializer):
    """Someone in a lunch, as the others in it see them: name and email."""

    class Meta(UserSerializer.Meta):
        fields = ["id", "name", "email"]
        read_only_fields = fields


class LunchDetailSerializer(LunchSerializer):
    """Everything on the live board. Expects `lunches.queries.with_board` prefetch."""

    # Who is in the lunch: the host, then everyone who joined by link, picked or not.
    people = serializers.SerializerMethodField()

    items = ItemSerializer(many=True, read_only=True)
    orders = SwiggyOrderSerializer(many=True, read_only=True)
    skipped = PersonSerializer(source="skipped_by", many=True, read_only=True)
    completed = PersonSerializer(source="completed_by", many=True, read_only=True)
    total_paise = serializers.SerializerMethodField()

    class Meta(LunchSerializer.Meta):
        fields = [
            *LunchSerializer.Meta.fields,
            "people",
            "items",
            "skipped",
            "completed",
            "orders",
            "total_paise",
        ]
        read_only_fields = fields

    def get_people(self, lunch: OrderSession) -> list[dict]:
        # Memberships are visible to the lunch's own org; the caller already takes
        # part in this lunch (that is how they got its board), so read them as it.
        with tenancy.reading_org(lunch.org_id):
            joined = [
                m.user
                for m in SessionMember.objects.filter(session_id=lunch.id)
                .select_related("user")
                .order_by("user__name")
            ]
        people = [lunch.host, *(u for u in joined if u.id != lunch.host_id)]
        return list(ContactSerializer(people, many=True).data)

    def get_total_paise(self, lunch: OrderSession) -> int:
        return sum(item.total_paise for item in lunch.items.all())
