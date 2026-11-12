from rest_framework import serializers

from apps.accounts.models import (
    Invite,
    Notification,
    OfficeAddress,
    Organization,
    Permission,
    User,
)
from apps.accounts.services import is_public_email


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "email_domain", "kind"]
        read_only_fields = ["id", "email_domain", "kind"]


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name", "email", "permissions"]
        read_only_fields = fields


class MeSerializer(UserSerializer):
    org = OrganizationSerializer(read_only=True)
    # A public email (Gmail and the like) hosts a personal party, not a company.
    public_email = serializers.SerializerMethodField()

    class Meta(UserSerializer.Meta):
        fields = [*UserSerializer.Meta.fields, "org", "public_email"]
        read_only_fields = fields

    def get_public_email(self, user: User) -> bool:
        return is_public_email(user.email)


class PermissionsSerializer(serializers.Serializer):
    permissions = serializers.ListField(
        child=serializers.ChoiceField(choices=Permission.choices),
        max_length=len(Permission),
    )

    def validate_permissions(self, value: list[str]) -> list[str]:
        return sorted(set(value))


class NotificationSerializer(serializers.ModelSerializer):
    read = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ["id", "title", "body", "link", "read", "created_at"]

    def get_read(self, notification: Notification) -> bool:
        return notification.read_at is not None


class MarkReadSerializer(serializers.Serializer):
    # Omit `ids` to mark everything read.
    ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, max_length=200
    )


class PushTokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=4096)


class OfficeAddressSerializer(serializers.ModelSerializer):
    address = serializers.CharField(max_length=300, trim_whitespace=True)

    class Meta:
        model = OfficeAddress
        fields = ["id", "address"]
        read_only_fields = ["id"]


class CompanySettingsSerializer(serializers.ModelSerializer):
    """GET and PATCH /api/company/settings. The email domain decides who can join, so
    it never changes."""

    name = serializers.CharField(max_length=120, trim_whitespace=True)
    team_name = serializers.CharField(
        max_length=120, trim_whitespace=True, allow_blank=True, required=False
    )
    radius_km = serializers.FloatField(min_value=0.5, max_value=8.0, required=False)
    office_lat = serializers.FloatField(
        min_value=-90, max_value=90, allow_null=True, required=False
    )
    office_lng = serializers.FloatField(
        min_value=-180, max_value=180, allow_null=True, required=False
    )
    addresses = OfficeAddressSerializer(many=True, read_only=True)

    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "team_name",
            "email_domain",
            "auto_join",
            "restaurant_mode",
            "radius_km",
            "office_lat",
            "office_lng",
            "addresses",
        ]
        read_only_fields = ["id", "email_domain", "addresses"]

    def validate(self, attrs):
        def value(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None)

        if (value("office_lat") is None) != (value("office_lng") is None):
            raise serializers.ValidationError(
                {"office_lat": "Give the office's latitude and longitude together."}
            )
        return attrs


class InviteSerializer(serializers.ModelSerializer):
    invited_by = serializers.CharField(source="invited_by.name", default=None)
    accepted = serializers.SerializerMethodField()

    class Meta:
        model = Invite
        fields = ["id", "email", "invited_by", "accepted", "created_at"]
        read_only_fields = fields

    def get_accepted(self, invite: Invite) -> bool:
        return invite.accepted_at is not None


class InviteRequestSerializer(serializers.Serializer):
    emails = serializers.ListField(
        child=serializers.EmailField(), min_length=1, max_length=50
    )
