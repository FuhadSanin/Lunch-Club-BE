from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import services
from apps.accounts.models import Invite, OfficeAddress, Organization, Permission, User
from apps.accounts.permissions import CompanyAPIView
from apps.accounts.serializers import (
    CompanySettingsSerializer,
    InviteRequestSerializer,
    InviteSerializer,
    MeSerializer,
    OfficeAddressSerializer,
    OrganizationSerializer,
    PermissionsSerializer,
    PushTokenSerializer,
    UserSerializer,
)
from apps.core import notify


class MeView(APIView):
    """GET /api/me: the signed-in person and their company (null until set up)."""

    def get(self, request):
        return Response(MeSerializer(request.user).data)


class CompanyView(APIView):
    """POST /api/company: set up the company for the signed-in person's email domain."""

    def post(self, request):
        serializer = OrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org = services.create_company(request.user, serializer.validated_data["name"])
        return Response(
            OrganizationSerializer(org).data, status=status.HTTP_201_CREATED
        )


class PushTokenView(CompanyAPIView):
    """POST /api/me/push-token: get this device's lunch notifications."""

    required_permissions = {"POST": None}  # your own device

    def post(self, request):
        serializer = PushTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notify.subscribe_to_company(
            serializer.validated_data["token"], request.user.org_id
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MembersView(CompanyAPIView):
    """GET /api/company/members: everyone in the company, with their permissions."""

    required_permissions = {"GET": Permission.MEMBER_READ}

    def get(self, request):
        members = User.objects.filter(org_id=request.user.org_id).order_by(
            "name", "email"
        )
        return Response(UserSerializer(members, many=True).data)


class MemberPermissionsView(CompanyAPIView):
    """PUT /api/company/members/<id>/permissions: set what a teammate may do."""

    required_permissions = {"PUT": Permission.MEMBER_UPDATE}

    def put(self, request, member_id):
        member = get_object_or_404(User, id=member_id, org_id=request.user.org_id)
        serializer = PermissionsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = services.set_permissions(
            request.user, member, serializer.validated_data["permissions"]
        )
        return Response(UserSerializer(member).data)


class PermissionsView(CompanyAPIView):
    """GET /api/permissions: every permission there is, and what it allows."""

    required_permissions = {"GET": None}

    def get(self, request):
        return Response(
            [{"code": code, "description": label} for code, label in Permission.choices]
        )


class CompanySettingsView(CompanyAPIView):
    """GET /api/company/settings: the company, its lunch defaults and office addresses.

    PATCH: change them.
    """

    required_permissions = {
        "GET": None,  # hosts need the defaults and addresses to start a lunch
        "PATCH": Permission.COMPANY_UPDATE,
    }

    def company(self, request) -> Organization:
        return Organization.objects.prefetch_related("addresses").get(
            id=request.user.org_id
        )

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
    """POST /api/company/addresses: add an office address."""

    required_permissions = {"POST": Permission.COMPANY_UPDATE}

    def post(self, request):
        serializer = OfficeAddressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        address = serializer.validated_data["address"]
        if OfficeAddress.objects.filter(
            org_id=request.user.org_id, address__iexact=address
        ).exists():
            return Response(
                {"address": ["This address is already saved."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        saved = OfficeAddress.objects.create(
            org_id=request.user.org_id, address=address
        )
        return Response(
            OfficeAddressSerializer(saved).data, status=status.HTTP_201_CREATED
        )


class AddressView(CompanyAPIView):
    """DELETE /api/company/addresses/<id>: remove an office address."""

    required_permissions = {"DELETE": Permission.COMPANY_UPDATE}

    def delete(self, request, address_id):
        address = get_object_or_404(
            OfficeAddress, id=address_id, org_id=request.user.org_id
        )
        address.delete()
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
