from datetime import timedelta
from urllib.parse import quote

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.serializers import AddressSerializer, UserSerializer
from apps.core import tenancy
from apps.lunches.checkout import checkout_carts
from apps.lunches.models import Order, OrderItem, OrderSession, SessionMember

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
    # Swiggy image links can hold spaces ("_1025860 (1).jpg"): encode them, and if a
    # link still isn't one, drop the photo rather than refuse the lunch over it.
    photo = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    def validate_photo(self, value: str) -> str:
        url = quote(value.strip(), safe=":/?#[]@!$&'()*+,;=%~")
        try:
            URLValidator(schemes=["http", "https"])(url)
        except DjangoValidationError:
            return ""
        return url


class JoiningSerializer(serializers.Serializer):
    open = serializers.BooleanField()


class StartLunchSerializer(serializers.ModelSerializer):
    name = serializers.CharField(
        max_length=80, trim_whitespace=True, allow_blank=True, required=False
    )
    # Where to deliver: the address (and where it is, when picked from the place
    # suggestions). It is kept in the company's addresses, which the lunch points at.
    address = AddressSerializer()
    # many=True builds a ListSerializer, which takes max_length; the stubs don't know.
    offered_restaurants = OfferedRestaurantSerializer(  # type: ignore[call-arg]
        many=True, required=False, max_length=100
    )

    # The host's local menus to offer too (see LocalMenu); the server copies their
    # dishes into the list, so a client can't make up a menu or a price.
    local_menu_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, max_length=20
    )

    class Meta:
        model = OrderSession
        fields = [
            "name",
            "cutoff_at",
            "address",
            "offered_restaurants",
            "local_menu_ids",
        ]

    def validate_cutoff_at(self, value):
        now = timezone.now()
        if not now < value <= now + MAX_LEAD_TIME:
            raise serializers.ValidationError("Pick a cutoff within the next 24 hours.")
        return value


class ItemSerializer(serializers.ModelSerializer):
    user = PersonSerializer(read_only=True)
    restaurant_name = serializers.CharField(max_length=200, trim_whitespace=True)
    # Swiggy's number, or "local-<id>" for a restaurant from a host's local menu.
    restaurant_id = serializers.RegexField(
        r"^(\d{1,20}|local-[0-9a-f-]{36})$",
        allow_blank=True,
        required=False,
        default="",
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


class TickSerializer(serializers.Serializer):
    """PUT /api/lunches/<id>/ticks: one thing the host ticks while running the lunch."""

    kind = serializers.ChoiceField(
        choices=["reviewed_restaurant", "reviewed_person", "arrived", "handed_out"]
    )
    # A restaurant's name (as people picked from it), or a person's id.
    key = serializers.CharField(max_length=200, trim_whitespace=False)
    done = serializers.BooleanField()


class PicksSerializer(serializers.Serializer):
    """PUT /api/lunches/<id>/picks: your whole cart, and whether it's complete."""

    # many=True builds a ListSerializer, which takes max_length; the stubs don't know.
    items = ItemSerializer(many=True, max_length=50)  # type: ignore[call-arg]
    complete = serializers.BooleanField(default=False)


class OrderSerializer(serializers.ModelSerializer):
    restaurants = serializers.SerializerMethodField()
    carts = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "status",
            "platform_order_id",
            "restaurants",
            "carts",
            "restaurant_count",
            "total_paise",
            "placed_at",
            "last_error",
            "bot_steps",
        ]
        read_only_fields = fields

    def get_restaurants(self, order: Order) -> list[str]:
        return sorted({item.restaurant_name for item in order.items.all()})

    def get_carts(self, order: Order) -> list[dict]:
        return checkout_carts(order.items.all())


class LunchSerializer(serializers.ModelSerializer):
    """Pass `context={"user": the caller}` to fill in `joined`."""

    host = PersonSerializer(read_only=True)
    address = AddressSerializer(read_only=True)
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
            "address",
            "offered_restaurants",
            "joining_open",
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
    orders = OrderSerializer(many=True, read_only=True)
    skipped = serializers.SerializerMethodField()
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
            "reviewed_restaurants",
            "reviewed_people",
            "arrived_restaurants",
            "handed_out_people",
        ]
        read_only_fields = fields

    def get_people(self, lunch: OrderSession) -> list[dict]:
        # Memberships are visible to the lunch's own org; the caller already takes
        # part in this lunch (that is how they got its board), so read them as it.
        # Someone of that org already reads as it, which saves three round trips.
        user = self.context.get("user")
        members = (
            SessionMember.objects.filter(session_id=lunch.id)
            .select_related("user")
            .order_by("user__name")
        )
        if user is not None and user.org_id == lunch.org_id:
            joined = [m.user for m in members]
        else:
            with tenancy.reading_org(lunch.org_id):
                joined = [m.user for m in members]
        people = [lunch.host, *(u for u in joined if u.id != lunch.host_id)]
        return list(ContactSerializer(people, many=True).data)

    def get_skipped(self, lunch: OrderSession) -> list[dict]:
        """People sitting this lunch out: completed, with nothing picked."""
        picked = {item.user_id for item in lunch.items.all()}
        skipped = [p for p in lunch.completed_by.all() if p.id not in picked]
        return list(PersonSerializer(skipped, many=True).data)

    def get_total_paise(self, lunch: OrderSession) -> int:
        return sum(item.total_paise for item in lunch.items.all())
