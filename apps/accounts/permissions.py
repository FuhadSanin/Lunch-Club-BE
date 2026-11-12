from typing import ClassVar

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.views import APIView

from apps.accounts.models import MEMBER_PERMISSIONS, Permission
from apps.accounts.services import is_public_email
from apps.core import tenancy

# What a view needs for each HTTP method; None means anyone in the company.
RequiredPermissions = dict[str, Permission | None]


class HasCompany(BasePermission):
    message = "Set up or join your company first."

    def has_permission(self, request, view) -> bool:
        return request.user.org_id is not None


class HasRequiredPermission(BasePermission):
    """Checks the permission the view names for this method in `required_permissions`.

    A method the view doesn't name is refused, so a new endpoint stays closed until it
    says what it needs.
    """

    message = "This action isn't available."

    def has_permission(self, request, view) -> bool:
        method = "GET" if request.method == "HEAD" else request.method
        required: RequiredPermissions = getattr(view, "required_permissions", {})
        if method not in required:
            return False
        permission = required[method]
        if permission is None or request.user.has_perm(permission):
            return True
        self.message = f"You need the {permission} permission to do this."
        return False


class CompanyAPIView(APIView):
    """Base view for company data: needs a company, and scopes the database to it.

    Subclasses name the permission each of their methods needs in
    `required_permissions`.
    """

    permission_classes = [IsAuthenticated, HasCompany, HasRequiredPermission]
    required_permissions: ClassVar[RequiredPermissions] = {}

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        tenancy.activate(request.user.org_id, request.user.id)


class MayTakePart(BasePermission):
    """First check for views on a lunch someone may be part of without being in its org.

    Passes when the person has the permission, or when it is one every participant has
    (the member set): which of the two applies depends on the lunch, so the view checks
    it again with `check_on_lunch` once it has found the lunch.
    """

    message = "This action isn't available."

    def has_permission(self, request, view) -> bool:
        method = "GET" if request.method == "HEAD" else request.method
        required: RequiredPermissions = getattr(view, "required_permissions", {})
        if method not in required:
            return False
        permission = required[method]
        if (
            permission is None
            or request.user.has_perm(permission)
            or permission in MEMBER_PERMISSIONS
        ):
            return True
        self.message = f"You need the {permission} permission to do this."
        return False


class ParticipantAPIView(APIView):
    """Base view for what a participant does on a lunch: read it and pick their food.

    The lunch may be their org's, or one they joined from outside. The database shows
    them only those (see apps.core.tenancy). Subclasses name the permission each method
    needs, and call `check_on_lunch` with the lunch they found.
    """

    permission_classes = [IsAuthenticated, MayTakePart]
    required_permissions: ClassVar[RequiredPermissions] = {}

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        tenancy.activate(request.user.org_id, request.user.id)

    def check_on_lunch(self, request, lunch) -> None:
        """In their own org the person needs the permission itself; on a lunch they
        joined they have the member set, whatever they can do in their own org."""
        method = "GET" if request.method == "HEAD" else request.method
        permission = self.required_permissions[method]
        if permission is None:
            return
        if lunch.org_id == request.user.org_id:
            allowed = request.user.has_perm(permission)
        else:
            allowed = permission in MEMBER_PERMISSIONS
        if not allowed:
            raise PermissionDenied(f"You need the {permission} permission to do this.")


class MayStartOwnLunch(MayTakePart):
    """Starting a lunch also needs no company when the person has a public email: their
    personal party is made for them."""

    def has_permission(self, request, view) -> bool:
        if (
            request.method == "POST"
            and request.user.org_id is None
            and is_public_email(request.user.email)
        ):
            return True
        return super().has_permission(request, view)
