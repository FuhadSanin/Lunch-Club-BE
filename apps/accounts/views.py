from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import erasure, rbac, services
from apps.accounts.auth import DevAuthentication, FirebaseAuthentication
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
from apps.accounts.permissions import CompanyAPIView
from apps.accounts.serializers import (
    AddressSerializer,
    ApiTokenSerializer,
    CompanySettingsSerializer,
    InviteRequestSerializer,
    InviteSerializer,
    MarkReadSerializer,
    MemberRolesSerializer,
    MeSerializer,
    NotificationSerializer,
    OrganizationSerializer,
    PushTokenSerializer,
    RoleSerializer,
    RoleWriteSerializer,
    UserSerializer,
)
from apps.core import tenancy
from apps.oauth import services as oauth


class MeView(APIView):
    """GET /api/me: the signed-in person and their company (null until set up).

    DELETE /api/me: delete the account and what identifies the person (erasure.py)."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        tenancy.activate(request.user.org_id, request.user.id)

    def get(self, request):
        return Response(MeSerializer(request.user).data)

    def delete(self, request):
        uid = erasure.delete_account(request.user)
        erasure.remove_sign_in(uid)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CompanyView(APIView):
    """POST /api/company: set up the company for the signed-in person's email domain."""

    def post(self, request):
        serializer = OrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org = services.create_company(request.user, serializer.validated_data["name"])
        return Response(
            OrganizationSerializer(org).data, status=status.HTTP_201_CREATED
        )


class PushTokenView(APIView):
    """POST /api/me/push-token: get this device's lunch notifications.
    DELETE: stop them on this device.

    For anyone signed in, in a company or not: the device hears its company, and the
    lunches of any personal party the person is in.
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        tenancy.activate(request.user.org_id, request.user.id)

    def post(self, request):
        serializer = PushTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.subscribe_device(request.user, serializer.validated_data["token"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    def delete(self, request):
        serializer = PushTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.unsubscribe_device(request.user, serializer.validated_data["token"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class TokensView(APIView):
    """GET /api/me/tokens: the assistants connected to your account (one row for each
    access token an assistant was given when you pressed Allow).

    There is one way to connect: the assistant signs in (apps/oauth). This needs a real
    sign-in: an access token can't list tokens.
    """

    authentication_classes = [FirebaseAuthentication, DevAuthentication]

    def get(self, request):
        mine = ApiToken.objects.filter(user=request.user, revoked_at__isnull=True)
        return Response(
            ApiTokenSerializer(mine.order_by("-created_at"), many=True).data
        )


class TokenView(APIView):
    """DELETE /api/me/tokens/<id>: disconnect an assistant (its token stops working)."""

    authentication_classes = [FirebaseAuthentication, DevAuthentication]

    def delete(self, request, token_id):
        row = get_object_or_404(
            ApiToken, id=token_id, user=request.user, revoked_at__isnull=True
        )
        oauth.disconnect(row)
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationsView(APIView):
    """GET /api/notifications: what you were told lately, newest first.

    For anyone signed in, in a company or not.
    """

    def get(self, request):
        mine = Notification.objects.filter(user=request.user).order_by("-created_at")
        return Response(
            {
                "unread": mine.filter(read_at__isnull=True).count(),
                "items": NotificationSerializer(mine[:30], many=True).data,
            }
        )


class NotificationsReadView(APIView):
    """POST /api/notifications/read: mark some (`ids`) or all as read."""

    def post(self, request):
        serializer = MarkReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        unread = Notification.objects.filter(user=request.user, read_at__isnull=True)
        if "ids" in serializer.validated_data:
            unread = unread.filter(id__in=serializer.validated_data["ids"])
        unread.update(read_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)


class MembersView(CompanyAPIView):
    """GET /api/company/members: everyone in the company, with their roles."""

    required_permissions = {"GET": Permission.MEMBER_READ}

    def get(self, request):
        members = (
            User.objects.filter(org_id=request.user.org_id)
            .prefetch_related("roles__grants")
            .order_by("name", "email")
        )
        return Response(UserSerializer(members, many=True).data)


class MemberRolesView(CompanyAPIView):
    """PUT /api/company/members/<id>/roles: set a teammate's roles (`roles`: ids)."""

    required_permissions = {"PUT": Permission.MEMBER_UPDATE}

    def put(self, request, member_id):
        member = get_object_or_404(User, id=member_id, org_id=request.user.org_id)
        serializer = MemberRolesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = rbac.set_roles(
            request.user, member, serializer.validated_data["roles"]
        )
        member = User.objects.prefetch_related("roles__grants").get(id=member.id)
        return Response(UserSerializer(member).data)


def company_roles(org_id):
    """The company's roles, with what each grants and how many people have it."""
    return (
        Role.objects.filter(org_id=org_id)
        .prefetch_related("grants")
        .annotate(members=Count("user_roles"))
        .order_by("-is_system", "name")
    )


class RolesView(CompanyAPIView):
    """GET /api/company/roles: the company's roles and what each allows.
    POST: make a role (`name`, `permissions`)."""

    required_permissions = {
        "GET": Permission.MEMBER_READ,
        "POST": Permission.MEMBER_UPDATE,
    }

    def get(self, request):
        roles = company_roles(request.user.org_id)
        return Response(RoleSerializer(roles, many=True).data)

    def post(self, request):
        serializer = RoleWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = rbac.create_role(
            request.user,
            serializer.validated_data["name"],
            serializer.validated_data["permissions"],
        )
        saved = company_roles(request.user.org_id).get(id=role.id)
        return Response(RoleSerializer(saved).data, status=status.HTTP_201_CREATED)


class RoleView(CompanyAPIView):
    """PATCH /api/company/roles/<id>: rename a role or change what it allows.
    DELETE: delete a role nobody has."""

    required_permissions = {
        "PATCH": Permission.MEMBER_UPDATE,
        "DELETE": Permission.MEMBER_UPDATE,
    }

    def patch(self, request, role_id):
        role = get_object_or_404(Role, id=role_id, org_id=request.user.org_id)
        serializer = RoleWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        rbac.update_role(
            request.user,
            role,
            name=serializer.validated_data.get("name"),
            permissions=serializer.validated_data.get("permissions"),
        )
        saved = company_roles(request.user.org_id).get(id=role.id)
        return Response(RoleSerializer(saved).data)

    def delete(self, request, role_id):
        role = get_object_or_404(Role, id=role_id, org_id=request.user.org_id)
        rbac.delete_role(request.user, role)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PermissionsView(CompanyAPIView):
    """GET /api/permissions: every permission there is, and what it allows."""

    required_permissions = {"GET": None}

    def get(self, request):
        return Response(
            [{"code": code, "description": label} for code, label in Permission.choices]
        )


class CompanySettingsView(CompanyAPIView):
    """GET /api/company/settings: the company, its lunch defaults and saved addresses.

    PATCH: change them.
    """

    required_permissions = {
        "GET": None,  # hosts need the defaults and addresses to start a lunch
        "PATCH": Permission.COMPANY_UPDATE,
    }

    def company(self, request) -> Organization:
        saved = Address.objects.filter(saved=True).order_by("created_at")
        return Organization.objects.prefetch_related(
            Prefetch("addresses", queryset=saved, to_attr="saved_addresses")
        ).get(id=request.user.org_id)

    def get(self, request):
        return Response(CompanySettingsSerializer(self.company(request)).data)

    def patch(self, request):
        serializer = CompanySettingsSerializer(
            self.company(request), data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(CompanySettingsSerializer(self.company(request)).data)


class AddressesView(CompanyAPIView):
    """POST /api/company/addresses: add an address to the list hosts choose from.

    Send `lat` and `lng` too when it was picked from the place suggestions."""

    required_permissions = {"POST": Permission.COMPANY_UPDATE}

    def post(self, request):
        serializer = AddressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        saved, newly_listed = services.save_address(
            request.user.org_id, data["address"], data.get("lat"), data.get("lng")
        )
        if not newly_listed:
            return Response(
                {"address": ["This address is already saved."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(AddressSerializer(saved).data, status=status.HTTP_201_CREATED)


class AddressView(CompanyAPIView):
    """DELETE /api/company/addresses/<id>: take an address off the list.

    Lunches that were delivered there keep it."""

    required_permissions = {"DELETE": Permission.COMPANY_UPDATE}

    def delete(self, request, address_id):
        address = get_object_or_404(Address, id=address_id, org_id=request.user.org_id)
        services.unsave_address(address)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitesView(CompanyAPIView):
    """GET /api/company/invites: who was invited. POST: invite people by email."""

    required_permissions = {
        "GET": Permission.MEMBER_READ,
        "POST": Permission.MEMBER_CREATE,
    }

    def get(self, request):
        invites = (
            Invite.objects.filter(org_id=request.user.org_id)
            .select_related("invited_by")
            .order_by("-created_at")
        )
        return Response(InviteSerializer(invites, many=True).data)

    def post(self, request):
        serializer = InviteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invites = services.invite(request.user, serializer.validated_data["emails"])
        return Response(
            InviteSerializer(invites, many=True).data, status=status.HTTP_201_CREATED
        )


class InviteView(CompanyAPIView):
    """DELETE /api/company/invites/<id>: withdraw an invite."""

    required_permissions = {"DELETE": Permission.MEMBER_DELETE}

    def delete(self, request, invite_id):
        invite = get_object_or_404(Invite, id=invite_id, org_id=request.user.org_id)
        invite.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
