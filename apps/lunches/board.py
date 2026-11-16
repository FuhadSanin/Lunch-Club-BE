"""The live board: a small Firestore document per lunch that browsers listen to.

It is a signal, not a copy: it says that the lunch changed (and its status), and the
browser then fetches the board from the API, which stays the only place the lunch's
contents are read from. Only the server writes these (see firestore.rules), always
after the change has committed, so nobody is told about a change that rolled back.
"""

import logging
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.core import tenancy
from apps.lunches.models import OrderSession, SessionMember

logger = logging.getLogger(__name__)


def publish(lunch: OrderSession) -> None:
    """Tell listeners the lunch changed, once the current transaction commits."""
    if settings.FIRESTORE_ENABLED:
        org_id, lunch_id = lunch.org_id, lunch.id
        transaction.on_commit(lambda: _write(org_id, lunch_id))


def member_uids(lunch: OrderSession) -> list[str]:
    """Who may listen to the board besides its company (see firestore.rules): the host
    and everyone who joined by the link. A personal party's guests come from other
    orgs, so the company check alone would shut them out."""
    guests = SessionMember.objects.filter(session=lunch).values("user_id")
    people = User.objects.filter(Q(id=lunch.host_id) | Q(id__in=guests))
    return sorted(people.values_list("firebase_uid", flat=True))


def _write(org_id: UUID, lunch_id: UUID) -> None:
    from firebase_admin import firestore

    from apps.core import firebase

    with tenancy.scoped(org_id):
        lunch = OrderSession.objects.get(id=lunch_id)
        board = {
            "status": lunch.status,
            # Changes on every write, so listeners hear about it even when the status
            # is the same (a new pick, a tick).
            "changed_at": timezone.now().isoformat(),
            # Who may listen (firestore.rules).
            "org_id": str(org_id),
            "member_uids": member_uids(lunch),
        }
    try:
        firestore.client(app=firebase.app()).collection("boards").document(
            str(lunch_id)
        ).set(board)
    except Exception:
        # The board is a display copy: a failed write must not fail the change itself.
        logger.exception(
            "Live board update failed", extra={"fields": {"lunch_id": lunch_id}}
        )
