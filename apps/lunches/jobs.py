from django.utils import timezone

from apps.core import tenancy
from apps.core.errors import RetryLater
from apps.core.jobs import job
from apps.lunches import services
from apps.lunches.models import OrderSession, SessionStatus


@job("lock-lunch")
def lock_lunch(payload: dict) -> None:
    """Runs at the cutoff time to close picks."""
    with tenancy.scoped(payload["org_id"]):
        lunch = (
            OrderSession.objects.select_for_update()
            .filter(id=payload["lunch_id"])
            .first()
        )
        if lunch is None:
            return
        if lunch.status == SessionStatus.OPEN and timezone.now() < lunch.cutoff_at:
            raise RetryLater("The cutoff has not passed yet.")
        services.lock(lunch)
