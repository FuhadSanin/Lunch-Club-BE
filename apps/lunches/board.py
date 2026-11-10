"""The live board: a Firestore copy of each lunch that browsers listen to.

PostgreSQL stays the source of truth. Only the server writes boards (see
firestore.rules), always after the change has committed, so the board never shows a
change that rolled back.
"""

import logging
from uuid import UUID

from django.conf import settings
from django.db import transaction

from apps.core import tenancy
from apps.lunches.models import OrderSession
from apps.lunches.queries import with_board
from apps.lunches.serializers import LunchDetailSerializer

logger = logging.getLogger(__name__)


def publish(lunch: OrderSession) -> None:
    """Refresh the lunch's board once the current transaction commits."""
    if settings.FIRESTORE_ENABLED:
        org_id, lunch_id = lunch.org_id, lunch.id
        transaction.on_commit(lambda: _write(org_id, lunch_id))


def _write(org_id: UUID, lunch_id: UUID) -> None:
    from firebase_admin import firestore

    from apps.core import firebase

    with tenancy.scoped(org_id):
        lunch = with_board(OrderSession.objects.filter(id=lunch_id)).get()
        board = {**LunchDetailSerializer(lunch).data, "org_id": str(org_id)}
    try:
        firestore.client(app=firebase.app()).collection("boards").document(
            str(lunch_id)
        ).set(board)
    except Exception:
        # The board is a display copy: a failed write must not fail the change itself.
        logger.exception(
            "Live board update failed", extra={"fields": {"lunch_id": lunch_id}}
        )
