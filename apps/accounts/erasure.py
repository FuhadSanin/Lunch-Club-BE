"""Deleting a person's account.

A person can ask us to delete what we hold about them (the DPDP Act asks for this,
and so does Swiggy's data policy). Everything that identifies them goes: their name
and email, the sign-in link, devices, notifications, assistant access, their saved
local menus, and their picks in food parties still taking picks. What other people
rely on stays, without a name: a finished food party keeps its record of who ordered
what, under "Former member", so the host's receipts and everyone else's totals do
not change.

Two things block it, because deleting would hurt other people: a food party they
host that is not over, and food of theirs that is already being ordered. They are
told which, and can delete once it is done.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from django.db import transaction

from apps.accounts.models import ApiToken, DeviceToken, Notification, User, UserRole
from apps.core.errors import Conflict

logger = logging.getLogger(__name__)

FORMER_MEMBER = "Former member"


def delete_account(user: User) -> str:
    """Erase the person. Returns their Firebase uid, so the sign-in can go too."""
    from apps.lunches.models import LocalMenu, OrderItem, OrderSession, SessionMember
    from apps.lunches.services import RUNNING, leave
    from apps.oauth.models import OAuthRefreshToken, OAuthRequest

    with transaction.atomic():
        if OrderSession.objects.filter(host=user, status__in=RUNNING).exists():
            raise Conflict(
                "You are hosting a food party that is not over yet. "
                "Close it or finish it first."
            )
        if (
            OrderItem.objects.filter(user=user, session__status__in=RUNNING)
            .exclude(session__status="open")
            .exists()
        ):
            raise Conflict(
                "Your food is already being ordered. "
                "You can delete your account once it arrives."
            )
        # Parties still taking picks: the person's picks leave with them.
        for session_id in list(
            SessionMember.objects.filter(user=user).values_list("session_id", flat=True)
        ):
            session = OrderSession.objects.filter(id=session_id).first()
            if session and session.org_id != user.org_id:
                leave(session, user)
        OrderItem.objects.filter(user=user, session__status="open").delete()

        LocalMenu.objects.filter(owner=user).delete()
        ApiToken.objects.filter(user=user).delete()
        OAuthRefreshToken.objects.filter(user=user).delete()
        OAuthRequest.objects.filter(user=user).delete()
        DeviceToken.objects.filter(user=user).delete()
        Notification.objects.filter(user=user).delete()
        UserRole.objects.filter(user=user).delete()

        uid = user.firebase_uid
        user.name = FORMER_MEMBER
        user.email = f"deleted-{user.id}@deleted.invalid"
        user.firebase_uid = f"deleted-{user.id}"
        user.org = None
        user.save(update_fields=["name", "email", "firebase_uid", "org", "updated_at"])
    return uid


def remove_sign_in(uid: str) -> None:
    """Delete the person's Firebase sign-in, so the same Google account starts fresh.

    This is tidying up after the account is already erased, so it never fails the
    request and never makes the person wait more than a few seconds.
    """

    def delete() -> None:
        from firebase_admin import auth

        from apps.core import firebase

        auth.delete_user(uid, app=firebase.app())

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        pool.submit(delete).result(timeout=3)
    except FutureTimeout:
        logger.warning("removing the sign-in of a deleted account took too long")
    except Exception:  # noqa: BLE001
        logger.warning("could not remove the sign-in of a deleted account")
    finally:
        pool.shutdown(wait=False)
