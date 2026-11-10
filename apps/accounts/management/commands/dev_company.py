from django.conf import settings
from django.core.management.base import BaseCommand

from apps.accounts.models import ALL_PERMISSIONS, Organization, User
from apps.accounts.services import dev_user_claims, email_domain, user_for_token


class Command(BaseCommand):
    help = (
        "Local development: create DEV_AUTH_EMAIL's company and give its user every "
        "permission."
    )

    def handle(self, *args, **options):
        if not settings.DEV_AUTH_EMAIL:
            return
        domain = email_domain(settings.DEV_AUTH_EMAIL)
        org, created = Organization.objects.get_or_create(
            email_domain=domain, defaults={"name": f"{domain} (dev)"}
        )
        user = user_for_token(dev_user_claims(settings.DEV_AUTH_EMAIL))
        User.objects.filter(id=user.id).update(permissions=sorted(ALL_PERMISSIONS))
        self.stdout.write(
            f"{'Created' if created else 'Found'} company {org.name}; "
            f"{user.email} has every permission"
        )
