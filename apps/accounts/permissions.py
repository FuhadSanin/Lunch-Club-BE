from typing import ClassVar

from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.views import APIView

from apps.accounts.models import Permission
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
        tenancy.activate(request.user.org_id)
