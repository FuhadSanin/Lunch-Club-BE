from rest_framework import serializers

from apps.accounts.models import (
    Address,
    ApiToken,
    Invite,
    Notification,
    Organization,
    Permission,
    Role,
    User,
)
from apps.accounts.services import is_public_email


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "email_domain", "kind"]
        read_only_fields = ["id", "email_domain", "kind"]


class RoleNameSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "name"]
        read_only_fields = fields


class UserSerializer(serializers.ModelSerializer):
    """A person with their roles, and the permissions those roles give them together.
    Expects `prefetch_related("roles__grants")` when listing many."""

    roles = RoleNameSerializer(many=True, read_only=True)
    permissions = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = User
        fields = ["id", "name", "email", "roles", "permissions"]
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


class RoleSerializer(serializers.ModelSerializer):
    """A role with what it grants and how many people have it. Expects
    `prefetch_related("grants")` and a `members` count annotation when listing."""

    system = serializers.BooleanField(source="is_system", read_only=True)
    permissions = serializers.SerializerMethodField()
    members = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Role
        fields = ["id", "name", "system", "permissions", "members"]
        read_only_fields = fields

    def get_permissions(self, role: Role) -> list[str]:
        return sorted(grant.permission for grant in role.grants.all())


class RoleWriteSerializer(serializers.Serializer):
    """POST and PATCH /api/company/roles: a name and the permissions it grants."""

    name = serializers.CharField(max_length=60, trim_whitespace=True)
    permissions = serializers.ListField(
        child=serializers.ChoiceField(choices=Permission.choices),
        max_length=len(Permission),
    )

    def validate_permissions(self, value: list[str]) -> list[str]:
        return sorted(set(value))


class MemberRolesSerializer(serializers.Serializer):
    roles = serializers.ListField(
        child=serializers.UUIDField(), max_length=50, allow_empty=True
    )


class NotificationSerializer(serializers.ModelSerializer):
    read = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ["id", "title", "body", "read", "created_at"]

    def get_read(self, notification: Notification) -> bool:
        return notification.read_at is not None


class MarkReadSerializer(serializers.Serializer):
    # Omit `ids` to mark everything read.
    ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, max_length=200
    )


class ApiTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiToken
        fields = ["id", "name", "prefix", "created_at", "last_used_at", "expires_at"]


class PushTokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=4096)


class AddressSerializer(serializers.ModelSerializer):
    """An address, with where it is when it was picked from the place suggestions."""

    address = serializers.CharField(max_length=300, trim_whitespace=True)
    lat = serializers.FloatField(
        min_value=-90, max_value=90, allow_null=True, required=False
    )
    lng = serializers.FloatField(
        min_value=-180, max_value=180, allow_null=True, required=False
    )

    class Meta:
        model = Address
        fields = ["id", "address", "lat", "lng"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        if (attrs.get("lat") is None) != (attrs.get("lng") is None):
            raise serializers.ValidationError(
                {"lat": "Give the address's latitude and longitude together."}
            )
        return attrs


class CompanySettingsSerializer(serializers.ModelSerializer):
    """GET and PATCH /api/company/settings. The email domain decides who can join, so
    it never changes."""

    name = serializers.CharField(max_length=120, trim_whitespace=True)
    team_name = serializers.CharField(
        max_length=120, trim_whitespace=True, allow_blank=True, required=False
    )
    addresses = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "team_name",
            "email_domain",
            "auto_join",
            "addresses",
        ]
        read_only_fields = ["id", "email_domain", "addresses"]

    def get_addresses(self, org: Organization) -> list[dict]:
        """The ones on the host's list. Expects the saved ones prefetched as
        `saved_addresses`."""
        saved = org.saved_addresses  # type: ignore[attr-defined]
        return list(AddressSerializer(saved, many=True).data)


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
