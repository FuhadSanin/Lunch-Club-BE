from django.conf import settings
from django.core.management.base import BaseCommand

from apps.accounts import rbac
from apps.accounts.models import HOST_ROLE, Organization, UserRole
from apps.accounts.services import dev_user_claims, email_domain, user_for_token


class Command(BaseCommand):
    help = (
        "Local development: create DEV_AUTH_EMAIL's company and give its user the Host "
        "role (every permission)."
    )

    def handle(self, *args, **options):
        if not settings.DEV_AUTH_EMAIL:
            return
        domain = email_domain(settings.DEV_AUTH_EMAIL)
        org, created = Organization.objects.get_or_create(
            email_domain=domain, defaults={"name": f"{domain} (dev)"}
        )
        user = user_for_token(dev_user_claims(settings.DEV_AUTH_EMAIL))
        if user.org_id is None:
            user.org = org
            user.save(update_fields=["org", "updated_at"])
        if created:
            Organization.objects.filter(id=org.id).update(created_by=user)
        host = rbac.ensure_system_roles(org)[HOST_ROLE]
        UserRole.objects.filter(user=user).exclude(role=host).delete()
        rbac.give_role(user, host)
        self.stdout.write(
            f"{'Created' if created else 'Found'} company {org.name}; "
            f"{user.email} has the Host role"
        )
